from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.controllers import moderation
from tests.controllers.test_comments import reply
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.helpers import codes, me, post_json, register, run_published

if TYPE_CHECKING:
    import pytest
    from dramatiq import Message
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def setup(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar], **community: str
) -> tuple[str, str]:
    """Alice moderates /in/pics with one link, t3_1; bob is an ordinary user."""
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice, **community)
    await submitted(client, alice, title='first post', url='a.org')
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    return alice, bob


def titles(listing: dict[str, Any]) -> list[str]:
    return [
        child['data'].get('title') or child['data'].get('body')
        for child in listing['data']['children']
    ]


async def act(client: AsyncTestClient[Litestar], path: str, modhash: str, **form: str) -> Any:  # noqa: ANN401
    return await client.post(path, data={'uh': modhash, 'api_type': 'json', **form})


async def modlog(client: AsyncTestClient[Litestar]) -> list[str]:
    actions = (await client.get('/in/pics/about/log.json')).json()['data']['children']
    return [a['data']['action'] for a in actions]


async def test_remove_spam_and_approve(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    assert (await act(client, '/api/remove', alice, id='t3_1', spam='false')).status_code == 200
    assert titles((await other_client.get('/in/pics/new.json')).json()) == []
    spam = (await client.get('/in/pics/about/spam.json')).json()
    assert titles(spam) == ['first post']
    assert spam['data']['children'][0]['data']['removed'] is True
    assert (await act(client, '/api/approve', alice, id='t3_1')).status_code == 200
    assert titles((await other_client.get('/in/pics/new.json')).json()) == ['first post']
    assert (await client.get('/in/pics/about/spam.json')).json()['data']['children'] == []
    assert await modlog(client) == ['approvelink', 'removelink']

    refused = await act(other_client, '/api/remove', bob, id='t3_1')
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('MOD_REQUIRED', None)]
    assert (await other_client.get('/in/pics/about/modqueue')).status_code == 403


async def test_removed_comments(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    await reply(other_client, bob, 't3_1', 'rude words')
    await act(client, '/api/remove', alice, id='t1_1')
    assert '[removed]' in (await other_client.get('/in/pics/comments/1/')).text
    mod_view = await client.get('/in/pics/comments/1/')
    assert 'rude words' in mod_view.text
    # r2's /api/remove marks as spam unless told otherwise; moderators see which.
    assert 'spam</li>' in mod_view.text
    assert await modlog(client) == ['spamcomment']


async def test_reports(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    await act(
        other_client, '/api/report', bob, thing_id='t3_1', reason='other', other_reason='spammy'
    )
    await act(other_client, '/api/report', bob, thing_id='t3_1', reason='twice')
    reports = (await client.get('/in/pics/about/reports.json')).json()['data']['children']
    assert [(r['data']['num_reports'], r['data']['report_reasons']) for r in reports] == [
        (1, ['spammy'])
    ]
    assert titles((await client.get('/in/pics/about/modqueue.json')).json()) == ['first post']
    # Ordinary readers never see reports.
    public = (await other_client.get('/in/pics.json')).json()['data']['children'][0]['data']
    assert (public['num_reports'], public['report_reasons']) == (None, None)

    await act(client, '/api/ignore_reports', alice, id='t3_1')
    assert (await client.get('/in/pics/about/reports.json')).json()['data']['children'] == []
    await act(client, '/api/unignore_reports', alice, id='t3_1')
    await act(client, '/api/approve', alice, id='t3_1')
    assert (await client.get('/in/pics/about/reports.json')).json()['data']['children'] == []


async def test_unmoderated(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client)
    assert titles((await client.get('/in/pics/about/unmoderated.json')).json()) == ['first post']
    await act(client, '/api/approve', alice, id='t3_1')
    assert (await client.get('/in/pics/about/unmoderated.json')).json()['data']['children'] == []


async def test_bans(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice, bob = await setup(client, other_client)
    banned = await post_json(
        client,
        '/api/friend',
        {
            'uh': alice,
            'community': 'pics',
            'type': 'banned',
            'name': 'bob',
            'ban_reason': 'spam',
            'note': 'third strike',
            'duration': '3',
            'ban_message': 'please stop',
        },
    )
    assert banned['errors'] == []
    await run_published(published)
    inbox = (await other_client.get('/message/inbox.json')).json()['data']['children']
    assert inbox[0]['data']['subject'] == 'banned from in/pics'
    assert 'please stop' in inbox[0]['data']['body']
    banlist = (await client.get('/in/pics/about/banned.json')).json()
    assert [(u['name'], u['note']) for u in banlist['data']['children']] == [
        ('bob', 'spam: third strike')
    ]

    submit = await post_json(
        other_client,
        '/api/submit',
        {'uh': bob, 'community': 'pics', 'kind': 'link', 'title': 't', 'url': 'b.org'},
    )
    assert codes(submit) == [('COMMUNITY_NOTALLOWED', 'community')]
    comment = await act(other_client, '/api/comment', bob, thing_id='t3_1', text='hi')
    assert codes(comment.json()['json']) == [('COMMUNITY_NOTALLOWED', 'parent')]
    # Banned users can still read and vote, as on r2.
    assert (await other_client.get('/in/pics')).status_code == 200
    assert (await post_json(other_client, '/api/vote', {'uh': bob, 'id': 't3_1', 'dir': '1'}))[
        'errors'
    ] == []

    mods = await post_json(
        client, '/api/friend', {'uh': alice, 'community': 'pics', 'type': 'banned', 'name': 'alice'}
    )
    assert codes(mods) == [('CANT_RESTRICT_MODERATOR', 'name')]
    await post_json(
        client, '/api/unfriend', {'uh': alice, 'community': 'pics', 'type': 'banned', 'name': 'bob'}
    )
    assert (await reply(other_client, bob, 't3_1', 'thanks'))['kind'] == 't1'
    assert await modlog(client) == ['unbanuser', 'banuser']
    assert (await other_client.get('/in/pics/about/banned')).status_code == 403


async def test_approved_submitters(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client, type='private')
    assert (await other_client.get('/in/pics')).status_code == 403
    await post_json(
        client,
        '/api/friend',
        {'uh': alice, 'community': 'pics', 'type': 'contributor', 'name': 'bob'},
    )
    assert (await other_client.get('/in/pics')).status_code == 200
    await submitted(other_client, bob, title='by bob', url='b.org')
    members = (await client.get('/in/pics/about/contributors.json')).json()['data']['children']
    assert [m['name'] for m in members] == ['bob']
    await post_json(
        client,
        '/api/unfriend',
        {'uh': alice, 'community': 'pics', 'type': 'contributor', 'name': 'bob'},
    )
    assert (await other_client.get('/in/pics')).status_code == 403


async def test_moderator_invites_and_permissions(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    published: list[Message[Any]],
) -> None:
    alice, bob = await setup(client, other_client)
    invite = await post_json(
        client,
        '/api/friend',
        {
            'uh': alice,
            'community': 'pics',
            'type': 'moderator_invite',
            'name': 'bob',
            'permissions': '-all,+posts',
        },
    )
    assert invite['errors'] == []
    await run_published(published)
    assert (await me(other_client)).json()['inbox_count'] == 1
    page = await other_client.get('/in/pics/about/moderators')
    assert "You're invited to help moderate in/pics." in page.text
    await post_json(other_client, '/api/accept_moderator_invite', {'uh': bob, 'community': 'pics'})
    mods = (await client.get('/in/pics/about/moderators.json')).json()['data']['children']
    assert [(m['name'], m['mod_permissions']) for m in mods] == [
        ('alice', ['all']),
        ('bob', ['posts']),
    ]

    # bob may act on posts, but not settings, bans or other moderators.
    assert (await act(other_client, '/api/remove', bob, id='t3_1')).status_code == 200
    assert (await other_client.get('/in/pics/about/edit')).status_code == 403
    assert (
        await act(other_client, '/api/friend', bob, community='pics', type='banned', name='alice')
    ).status_code == 403
    assert (
        await act(
            other_client, '/api/unfriend', bob, community='pics', type='moderator', name='alice'
        )
    ).status_code == 403

    await post_json(
        client,
        '/api/setpermissions',
        {
            'uh': alice,
            'community': 'pics',
            'type': 'moderator',
            'name': 'bob',
            'permissions': '+all',
        },
    )
    mods = (await client.get('/in/pics/about/moderators.json')).json()['data']['children']
    assert mods[1]['mod_permissions'] == ['all']
    bad = await post_json(
        client,
        '/api/setpermissions',
        {
            'uh': alice,
            'community': 'pics',
            'type': 'moderator',
            'name': 'bob',
            'permissions': '+everything',
        },
    )
    assert codes(bad) == [('INVALID_PERMISSIONS', 'permissions')]
    # Seniority: bob, added later, still cannot remove alice; bob can step down.
    assert (
        await act(
            other_client, '/api/unfriend', bob, community='pics', type='moderator', name='alice'
        )
    ).status_code == 403
    await post_json(other_client, '/api/leavemoderator', {'uh': bob, 'community': 'pics'})
    mods = (await client.get('/in/pics/about/moderators.json')).json()['data']['children']
    assert [m['name'] for m in mods] == ['alice']
    # Newest first. The bad setpermissions changed nothing, so it is not logged.
    assert await modlog(client) == [
        'removemoderator',
        'setpermissions',
        'spamlink',
        'acceptmoderatorinvite',
        'invitemoderator',
    ]


async def test_distinguish_sticky_and_lock(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    await submitted(client, alice, title='second post', url='b.org')
    assert titles((await client.get('/in/pics.json')).json())[0] == 'second post'
    await post_json(
        client, '/api/set_community_sticky', {'uh': alice, 'id': 't3_1', 'state': 'true'}
    )
    listing = (await other_client.get('/in/pics.json')).json()['data']['children']
    assert [(c['data']['title'], c['data']['stickied']) for c in listing] == [
        ('first post', True),
        ('second post', False),
    ]

    mine = await reply(client, alice, 't3_1', 'official word')
    await post_json(
        client,
        '/api/distinguish',
        {'uh': alice, 'id': mine['data']['id'], 'how': 'yes', 'sticky': 'true'},
    )
    thread = (await other_client.get('/in/pics/comments/1.json')).json()
    assert thread[1]['data']['children'][0]['data']['distinguished'] == 'moderator'
    not_mine = await reply(other_client, bob, 't3_1', 'mine')
    refused = await act(client, '/api/distinguish', alice, id=not_mine['data']['id'], how='yes')
    assert refused.status_code == 403

    await post_json(client, '/api/lock', {'uh': alice, 'id': 't3_1'})
    locked = await act(other_client, '/api/comment', bob, thing_id='t3_1', text='hello?')
    assert codes(locked.json()['json']) == [('THREAD_LOCKED', 'parent')]
    assert (await reply(client, alice, 't3_1', 'mods may still speak'))['kind'] == 't1'
    await post_json(client, '/api/unlock', {'uh': alice, 'id': 't3_1'})
    assert (await reply(other_client, bob, 't3_1', 'thanks'))['kind'] == 't1'
    assert {'sticky', 'distinguish', 'lock', 'unlock'} <= set(await modlog(client))


async def test_mark_nsfw(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    await submitted(other_client, bob, title="bob's", url='b.org')

    def nsfw(listing: dict[str, Any]) -> dict[str, bool]:
        return {c['data']['title']: c['data']['over_18'] for c in listing['data']['children']}

    # The author marks their own link, and it isn't logged.
    assert (await act(other_client, '/api/marknsfw', bob, id='t3_2')).status_code == 200
    assert nsfw((await client.get('/in/pics/new.json')).json())["bob's"] is True
    assert 'action="/api/unmarknsfw"' in (await other_client.get('/in/pics/new')).text
    # Others need to be innkeepers, whose changes are logged.
    assert (await act(other_client, '/api/marknsfw', bob, id='t3_1')).status_code == 403
    assert (await act(client, '/api/unmarknsfw', alice, id='t3_2')).status_code == 200
    assert nsfw((await client.get('/in/pics/new.json')).json()) == {
        "bob's": False,
        'first post': False,
    }
    assert await modlog(client) == ['unmarknsfw']
    assert (await act(client, '/api/marknsfw', alice, id='t1_9')).status_code == 400


async def test_links_stay_nsfw_in_nsfw_communities(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client, over_18='on')
    refused = await act(client, '/api/unmarknsfw', alice, id='t3_1')
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('COMMUNITY_NOTALLOWED', 'id')]


async def test_modlog_ids_and_pages(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice, _ = await setup(client, other_client)
    for path in ('/api/lock', '/api/unlock', '/api/lock'):
        await post_json(client, path, {'uh': alice, 'id': 't3_1'})
    monkeypatch.setattr(moderation, 'PAGE_SIZE', 2)
    first = (await client.get('/in/pics/about/log.json')).json()['data']
    ids = [child['data']['id'] for child in first['children']]
    # r2's ModAction_ and a UUID, version 7 so the log pages by it in time order.
    assert all(re.fullmatch(r'ModAction_[0-9a-f-]{14}7[0-9a-f-]{21}', id_) for id_ in ids)
    assert [c['data']['action'] for c in first['children']] == ['lock', 'unlock']
    assert first['after'] == ids[-1]
    rest = (await client.get(f'/in/pics/about/log.json?after={first["after"]}')).json()['data']
    assert [c['data']['action'] for c in rest['children']] == ['lock']
    assert rest['after'] is None
    # A cursor that can't be read starts from the top rather than failing.
    odd = await client.get('/in/pics/about/log.json?after=ModAction_12')
    assert len(odd.json()['data']['children']) == 2
