from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.controllers.test_communities import create
from tests.controllers.test_moderation import act, modlog
from tests.helpers import codes, post_json, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

PAGE = '# Welcome\n\nRead the [rules](#wiki_rules).\n\n## Rules\n\nBe nice.'


async def setup(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar], **community: str
) -> tuple[str, str]:
    """Alice moderates /in/pics, with an open wiki unless told otherwise; bob is a reader."""
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice, **{'wikimode': 'anyone', 'wiki_edit_karma': '0', **community})
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    return alice, bob


async def edit(
    client: AsyncTestClient[Litestar], modhash: str, page: str, content: str, **form: str
) -> Any:  # noqa: ANN401
    return await act(client, '/in/pics/api/wiki/edit', modhash, page=page, content=content, **form)


def errors(response: Any) -> list[tuple[Any, Any]]:  # noqa: ANN401
    return codes(response.json()['json'])


async def page_json(client: AsyncTestClient[Litestar], page: str) -> dict[str, Any]:
    body: dict[str, Any] = (await client.get(f'/in/pics/wiki/{page}.json')).json()['data']
    return body


async def test_write_read_and_history(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    _, bob = await setup(client, other_client)
    missing = await other_client.get('/in/pics/wiki/')
    assert missing.status_code == 404
    assert 'There\'s no page called "index" here yet.' in missing.text

    assert errors(await edit(other_client, bob, 'index', PAGE, reason='first')) == []
    page = await page_json(other_client, 'index')
    assert page['content_md'] == PAGE
    assert page['revision_by'] == {'kind': 't2', 'data': {'name': 'bob'}}
    # r2's table of contents, with ids named after the headers.
    assert '<div class="toc">' in page['content_html']
    assert '<h2 id="wiki_rules">Rules</h2>' in page['content_html']
    html = (await other_client.get('/in/pics/wiki/index')).text
    assert '<div class="md wiki">' in html
    assert 'class="author">bob</a>' in html

    first = page['revision_id']
    assert errors(await edit(other_client, bob, 'index', 'v2', previous=first)) == []
    # Saving again from the first revision would undo the second without knowing it.
    stale = await edit(other_client, bob, 'index', 'v3', previous=first)
    assert errors(stale) == [('EDIT_CONFLICT', 'content')]
    form = await other_client.post(
        '/in/pics/api/wiki/edit',
        data={'uh': bob, 'page': 'index', 'content': 'v3', 'previous': first},
    )
    assert 'the page as it is now' in form.text
    assert '>v2</textarea>' in form.text

    history = (await other_client.get('/in/pics/wiki/revisions/index.json')).json()['data']
    assert [(r['reason'], r['author']['data']['name']) for r in history['children']] == [
        (None, 'bob'),
        ('first', 'bob'),
    ]
    recent = (await other_client.get('/in/pics/wiki/revisions.json')).json()['data']['children']
    assert [r['page'] for r in recent] == ['index', 'index']
    pages = (await other_client.get('/in/pics/wiki/pages.json')).json()
    assert pages == {'kind': 'wikipagelisting', 'data': ['index']}

    older, newer = history['children'][1]['id'], history['children'][0]['id']
    old = await other_client.get('/in/pics/wiki/index.json', params={'v': older})
    assert old.json()['data']['content_md'] == PAGE
    compared = await other_client.get(f'/in/pics/wiki/index?v={older}&v2={newer}')
    assert '<table class="diff"' in compared.text
    assert 'the changes between' in compared.text


async def test_page_names(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    _, bob = await setup(client, other_client)
    moved = await other_client.get('/in/pics/wiki/Some Page', follow_redirects=False)
    assert moved.headers['location'] == '/in/pics/wiki/some_page'
    assert errors(await edit(other_client, bob, '../etc', 'x')) == [('INVALID_PAGE_NAME', 'page')]
    assert errors(await edit(other_client, bob, 'edit/x', 'x')) == [('INVALID_PAGE_NAME', 'page')]
    # Namespaces r2 kept for itself.
    refused = await edit(other_client, bob, 'ina/x', 'x')
    assert refused.status_code == 403
    assert errors(refused) == [('RESTRICTED_PAGE', 'page')]


async def test_who_may_edit(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client, wikimode='modonly')
    assert errors(await edit(client, alice, 'faq', 'answers')) == []
    # Anyone reads a mods-only wiki; only moderators and wiki contributors edit it.
    assert (await page_json(other_client, 'faq'))['may_revise'] is False
    refused = await edit(other_client, bob, 'faq', 'mine now')
    assert refused.status_code == 403
    assert errors(refused) == [('MAY_NOT_REVISE', 'page')]

    friend = {'uh': alice, 'community': 'pics', 'name': 'bob'}
    added = await post_json(client, '/api/friend', {**friend, 'type': 'wikicontributor'})
    assert added['errors'] == []
    assert errors(await edit(other_client, bob, 'faq', 'answers, and more')) == []
    contributors = (await client.get('/in/pics/about/wikicontributors.json')).json()
    assert [c['name'] for c in contributors['data']['children']] == ['bob']

    banned = await post_json(
        client, '/api/friend', {**friend, 'type': 'wikibanned', 'duration': '3', 'note': 'spam'}
    )
    assert banned['errors'] == []
    assert (await edit(other_client, bob, 'faq', 'again')).status_code == 403
    bans = (await client.get('/in/pics/about/wikibanned.json')).json()['data']['children']
    assert [(b['name'], b['note']) for b in bans] == [('bob', 'spam')]
    assert await modlog(client) == ['wikibanned', 'wikicontributor', 'wikirevise']

    # Bob may not manage the wiki's members.
    other = await other_client.post(
        '/api/friend',
        data={'uh': bob, 'community': 'pics', 'name': 'alice', 'type': 'wikicontributor'},
        headers={'Accept': 'application/json'},
    )
    assert other.status_code == 403


async def test_karma_and_age_minimums(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    _, bob = await setup(client, other_client, wiki_edit_karma='10')
    assert errors(await edit(other_client, bob, 'index', 'hi')) == [('MAY_NOT_REVISE', 'page')]


async def test_disabled_wiki(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client, wikimode='disabled')
    refused = await other_client.get('/in/pics/wiki/index.json')
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('WIKI_DISABLED', None)]
    # Moderators still use it, as on r2, and the wiki tab shows only to them.
    assert errors(await edit(client, alice, 'index', 'drafts')) == []
    assert 'href="/in/pics/wiki/" class="choice">wiki</a>' in (await client.get('/in/pics/')).text
    assert '>wiki</a>' not in (await other_client.get('/in/pics/')).text


async def test_page_settings_hide_and_revert(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    assert errors(await edit(client, alice, 'secret', 'mods only')) == []
    settings = await client.post(
        '/in/pics/wiki/settings/secret', data={'uh': alice, 'permlevel': '2', 'listed': ''}
    )
    assert settings.status_code == 200
    assert (await client.get('/in/pics/wiki/settings/secret.json')).json() == {
        'kind': 'wikipagesettings',
        'data': {'permlevel': 2, 'listed': False, 'editors': []},
    }
    assert (await other_client.get('/in/pics/wiki/secret')).status_code == 403
    assert (await other_client.get('/in/pics/wiki/pages.json')).json()['data'] == []
    assert await modlog(client) == ['wikipermlevel', 'wikipagelisted', 'wikirevise']

    # Level 1: only the page's own editors, besides moderators.
    await client.post('/in/pics/wiki/settings/secret', data={'uh': alice, 'permlevel': '1'})
    assert errors(await edit(other_client, bob, 'secret', 'bob was here')) == [
        ('MAY_NOT_REVISE', 'page')
    ]
    await act(client, '/in/pics/api/wiki/alloweditor/add', alice, page='secret', username='bob')
    assert errors(await edit(other_client, bob, 'secret', 'bob was here')) == []

    history = (await client.get('/in/pics/wiki/revisions/secret.json')).json()['data']['children']
    bobs, first = history[0]['id'], history[1]['id']
    hidden = await act(client, '/in/pics/api/wiki/hide', alice, page='secret', revision=bobs)
    assert hidden.json()['json']['data'] == {'status': True}
    seen = (await other_client.get('/in/pics/wiki/revisions/secret.json')).json()['data']
    assert [r['id'] for r in seen['children']] == [first]

    await act(client, '/in/pics/api/wiki/revert', alice, page='secret', revision=first)
    page = await page_json(client, 'secret')
    assert page['content_md'] == 'mods only'
    latest = (await client.get('/in/pics/wiki/revisions/secret.json')).json()['data']['children']
    assert latest[0]['reason'].startswith('reverted back')


async def test_config_pages_are_the_community_settings(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    # Before any wiki edit, the page shows what the settings page saved.
    assert (await page_json(other_client, 'config/sidebar'))['content_md'] == '**rules**: be nice'
    refused = await edit(other_client, bob, 'config/sidebar', 'bob rules')
    assert errors(refused) == [('MAY_NOT_REVISE', 'page')]

    assert errors(await edit(client, alice, 'config/sidebar', 'New **sidebar**')) == []
    sidebar = (await other_client.get('/in/pics/')).text
    assert 'New <strong>sidebar</strong>' in sidebar
    assert await modlog(client) == ['wikirevise']
    log = (await client.get('/in/pics/about/log.json')).json()['data']['children']
    assert log[0]['data']['details'] == 'changed the sidebar'

    # The settings page edits the same text, and the page keeps that in its history.
    from tests.controllers.test_communities import settings_form  # noqa: PLC0415

    form = settings_form(community='t5_1', description='from settings', wikimode='anyone')
    saved = await post_json(client, '/api/site_admin', {'uh': alice, **form})
    assert saved['errors'] == []
    history = (await client.get('/in/pics/wiki/revisions/config/sidebar.json')).json()
    assert [r['reason'] for r in history['data']['children']] == [
        'Updated from the community settings',
        None,
    ]
    assert (await page_json(client, 'config/sidebar'))['content_md'] == 'from settings'
