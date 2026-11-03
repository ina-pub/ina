from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_comments import reply, thread_json
from tests.controllers.test_listings import submitted
from tests.controllers.test_messages import children
from tests.controllers.test_moderation import act, modlog, setup, titles
from tests.helpers import PASSWORD, codes, post_json, run_published

if TYPE_CHECKING:
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

SORTING = """\
title: [cheap, pills]
action: spam
action_reason: "sells {{match}}"
---
domain: shady.example
action: filter
---
title: question
action: report
action_reason: a question?
"""
ANSWERING = """\
type: comment
body: help
comment: "Hello {{author}}, see the wiki."
---
type: submission
title: meta
set_locked: true
comment: Read the rules.
comment_stickied: true
modmail: "{{permalink}} needs a look"
modmail_subject: "meta post by {{author}}"
"""


async def save(client: AsyncTestClient[Litestar], modhash: str, rules: str) -> Any:  # noqa: ANN401
    """The rules live in the wiki, at config/bouncer, where r2 kept config/automoderator."""
    form = {'page': 'config/bouncer', 'content': rules}
    return await act(client, '/in/pics/api/wiki/edit', modhash, **form)


async def test_only_moderators_with_config_edit_the_rules(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    # An open wiki, so the page's own rule is what keeps bob out: only moderators see it.
    alice, bob = await setup(client, other_client, wikimode='anyone')
    assert (await other_client.get('/in/pics/wiki/config/bouncer')).status_code == 403
    refused = await save(other_client, bob, 'title: a\naction: remove')
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('MAY_NOT_VIEW', 'page')]

    bad = (await save(client, alice, 'title: a\naction: delete')).json()['json']
    assert codes(bad) == [('SPECIAL_ERRORS', 'content')]
    assert 'rule 1: unknown action "delete"' in bad['errors'][0][1]
    page = (await client.get('/in/pics/wiki/config/bouncer.json')).json()
    assert (page['kind'], page['data']['content_md'], page['data']['may_revise']) == (
        'wikipage',
        '',
        True,
    )

    saved = await save(client, alice, 'title: a\naction: remove')
    assert saved.json()['json']['errors'] == []
    page = await client.get('/in/pics/wiki/config/bouncer')
    assert 'title: a\naction: remove</code></pre>' in page.text
    assert await modlog(client) == ['wikirevise']
    # The old address of the editor leads to the wiki.
    moved = await client.get('/in/pics/about/bouncer', follow_redirects=False)
    assert moved.headers['location'] == '/in/pics/wiki/edit/config/bouncer'


async def test_nobody_can_register_as_bouncer(client: AsyncTestClient[Litestar]) -> None:
    for name in ('bouncer', 'AutoModerator'):
        form = {'user': name, 'passwd': PASSWORD, 'passwd2': PASSWORD, 'email': ''}
        body = await post_json(client, '/api/register', form)
        assert codes(body) == [('USERNAME_TAKEN', 'user')], name


async def test_removes_filters_and_reports(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice, bob = await setup(client, other_client)
    assert (await save(client, alice, SORTING)).json()['json']['errors'] == []
    published.clear()
    await submitted(other_client, bob, title='cheap pills here', url='a.example')
    await submitted(other_client, bob, title='nice photo', url='shady.example/1')
    await submitted(other_client, bob, title='a question', url='b.example')
    # A moderator's posts are exempt from rules that remove.
    await submitted(client, alice, title='cheap tricks', url='c.example')
    ran = await run_published(published)
    assert ran.count('bouncer_check') == 4

    assert titles((await other_client.get('/in/pics/new.json')).json()) == [
        'cheap tricks',
        'a question',
        'first post',
    ]
    assert titles((await client.get('/in/pics/about/spam.json')).json()) == [
        'nice photo',
        'cheap pills here',
    ]
    # Filtered things wait in the modqueue with reported ones.
    modqueue = (await client.get('/in/pics/about/modqueue.json')).json()
    assert titles(modqueue) == ['a question', 'nice photo']
    reported = modqueue['data']['children'][0]['data']
    assert reported['num_reports'] == 1
    assert reported['report_reasons'] == ['a question?']

    log = (await client.get('/in/pics/about/log.json')).json()['data']['children']
    by_bot = [(a['data']['mod'], a['data']['action'], a['data']['details']) for a in log][:2]
    assert by_bot == [('Bouncer', 'removelink', ''), ('Bouncer', 'spamlink', 'sells cheap')]


async def test_replies_locks_and_sends_modmail(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice, bob = await setup(client, other_client)
    assert (await save(client, alice, ANSWERING)).json()['json']['errors'] == []
    published.clear()
    asked = await reply(other_client, bob, 't3_1', 'help please')
    meta = await submitted(other_client, bob, title='a meta question', url='d.example')
    await run_published(published)

    thread = thread_json((await other_client.get('/in/pics/comments/1/.json')).json())
    answer = thread[0]['data']['replies']['data']['children'][0]['data']
    assert answer['body'] == 'Hello bob, see the wiki.'
    assert answer['author'] == 'Bouncer'
    assert answer['distinguished'] == 'moderator'
    assert answer['parent_id'] == asked['data']['id']
    # The reply reaches bob's inbox like any other.
    inbox = children((await other_client.get('/message/unread.json')).json())
    assert [(c['data']['author'], c['data']['body']) for c in inbox] == [
        ('Bouncer', 'Read the rules.'),
        ('Bouncer', 'Hello bob, see the wiki.'),
    ]

    link_id = meta.removeprefix('t3_')
    page = (await other_client.get(f'/in/pics/comments/{link_id}/.json')).json()
    assert page[0]['data']['children'][0]['data']['locked'] is True
    stickied = page[1]['data']['children'][0]['data']
    assert (stickied['body'], stickied['stickied']) == ('Read the rules.', True)
    assert (
        'pinned by the innkeepers' in (await other_client.get(f'/in/pics/comments/{link_id}/')).text
    )

    modmail = children((await client.get('/message/moderator.json')).json())
    assert [(m['data']['author'], m['data']['subject']) for m in modmail] == [
        ('Bouncer', 'meta post by bob')
    ]
    assert modmail[0]['data']['body'].endswith('/in/pics/comments/2/a_meta_question/ needs a look')
    assert 'lock' in await modlog(client)
