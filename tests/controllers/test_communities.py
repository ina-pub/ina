from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.helpers import codes, post_json, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


def settings_form(**overrides: str) -> dict[str, str]:
    """A complete settings form, as the page posts it."""
    return {
        'name': 'pics',
        'title': 'Pictures',
        'public_description': 'only *pictures*',
        'description': '**rules**: be nice',
        'type': 'public',
        'link_type': 'any',
        'show_media_preview': 'on',
        'allow_top': 'on',
        'spam_links': 'high',
        'spam_selfposts': 'high',
        'spam_comments': 'low',
        'comment_score_hide_mins': '0',
        'wikimode': 'disabled',
        'wiki_edit_karma': '100',
        'wiki_edit_age': '0',
        **overrides,
    }


async def create(
    client: AsyncTestClient[Litestar], modhash: str, **overrides: str
) -> dict[str, Any]:
    body = await post_json(client, '/api/site_admin', {'uh': modhash, **settings_form(**overrides)})
    assert body['errors'] == [], body
    return body


async def test_create_makes_the_creator_a_moderator(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    body = await create(client, modhash)
    assert body['data'] == {'name': 't5_1', 'url': '/in/pics/'}

    page = await client.get('/in/pics')
    assert page.status_code == 200
    assert '<title>Pictures</title>' in page.text
    assert '<div class="md"><p><strong>rules</strong>: be nice</p>' in page.text
    assert 'about/edit">settings' in page.text
    assert '<a href="/user/alice" class="author">alice</a>' in page.text

    about = (await client.get('/in/PICS/about.json')).json()
    assert about['kind'] == 't5'
    assert about['data']['display_name'] == 'pics'
    assert about['data']['public_description_html'] == '<p>only <em>pictures</em></p>\n'
    assert about['data']['user_is_moderator'] is True
    assert about['data']['subscribers'] == 0
    listing = (await client.get('/in/pics.json')).json()
    assert listing['kind'] == 'Listing'


async def test_create_without_js(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    response = await client.post(
        '/api/site_admin', data={'uh': modhash, **settings_form()}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers['location'] == '/in/pics/'
    bad = await client.post('/api/site_admin', data={'uh': modhash, **settings_form(title='')})
    assert bad.status_code == 200
    assert 'class="error NO_TEXT field-title"' in bad.text


async def test_create_errors(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    await create(client, modhash)
    taken = await post_json(
        client, '/api/site_admin', {'uh': modhash, **settings_form(name='PICS')}
    )
    assert codes(taken) == [('COMMUNITY_EXISTS', 'name')]
    reserved = await post_json(
        client, '/api/site_admin', {'uh': modhash, **settings_form(name='all')}
    )
    assert codes(reserved) == [('BAD_COMMUNITY_NAME', 'name')]


async def test_create_needs_login_and_csrf(client: AsyncTestClient[Litestar]) -> None:
    logged_out = await client.post('/api/site_admin', data={**settings_form(), 'api_type': 'json'})
    assert logged_out.status_code == 403
    assert codes(logged_out.json()['json']) == [('USER_REQUIRED', None)]
    await register(client, 'alice')
    no_csrf = await client.post('/api/site_admin', data={**settings_form(), 'api_type': 'json'})
    assert codes(no_csrf.json()['json']) == [('BAD_CSRF', None)]


async def test_only_moderators_edit(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    await create(client, modhash)
    edit = await client.get('/in/pics/about/edit.json')
    assert edit.json()['data']['title'] == 'Pictures'
    saved = await post_json(
        client,
        '/api/site_admin',
        {'uh': modhash, 'community': 't5_1', **settings_form(title='Pics!')},
    )
    assert saved['errors'] == []
    assert (await client.get('/in/pics/about.json')).json()['data']['title'] == 'Pics!'

    bob = (await register(other_client, 'bob'))['data']['modhash']
    assert (await other_client.get('/in/pics/about/edit')).status_code == 403
    denied = await other_client.post(
        '/api/site_admin',
        data={
            'uh': bob,
            'community': 't5_1',
            'api_type': 'json',
            **settings_form(title='mine now'),
        },
    )
    assert denied.status_code == 403
    assert codes(denied.json()['json']) == [('COMMUNITY_NOTALLOWED', 'community')]
    page = await other_client.get('/in/pics')
    assert 'about/edit">settings' not in page.text


async def test_subscribe(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    await create(client, modhash)
    bob = (await register(other_client, 'bob'))['data']['modhash']

    sub = await other_client.post(
        '/api/subscribe',
        data={'uh': bob, 'action': 'sub', 'community': 't5_1', 'dest': '/in/pics'},
        follow_redirects=False,
    )
    assert sub.headers['location'] == '/in/pics'
    about = (await other_client.get('/in/pics/about.json')).json()['data']
    assert (about['subscribers'], about['user_is_subscriber']) == (1, True)
    assert 'value="unsub"' in (await other_client.get('/in/pics')).text

    mine = (await other_client.get('/ins/mine.json')).json()['data']['children']
    assert [child['data']['display_name'] for child in mine] == ['pics']

    await post_json(
        other_client, '/api/subscribe', {'uh': bob, 'action': 'unsub', 'community_name': 'pics'}
    )
    assert (await other_client.get('/in/pics/about.json')).json()['data']['subscribers'] == 0


async def test_private_communities(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    await create(client, modhash, type='private')
    assert (await client.get('/in/pics')).status_code == 200
    page = await other_client.get('/in/pics')
    assert page.status_code == 403
    assert 'this community is private' in page.text
    data = await other_client.get('/in/pics.json')
    assert codes(data.json()['json']) == [('PRIVATE_COMMUNITY', None)]
    bob = (await register(other_client, 'bob'))['data']['modhash']
    refused = await other_client.post(
        '/api/subscribe', data={'uh': bob, 'action': 'sub', 'community': 't5_1', 'api_type': 'json'}
    )
    assert codes(refused.json()['json']) == [('COMMUNITY_NOTALLOWED', 'community')]


async def test_missing_community(client: AsyncTestClient[Litestar]) -> None:
    page = await client.get('/in/nowhere')
    assert page.status_code == 404
    assert 'there is no community by that name' in page.text
    assert codes((await client.get('/in/nowhere.json')).json()['json']) == [
        ('COMMUNITY_NOEXIST', None)
    ]


async def test_directory(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    # Only bbb is listed, so it alone is a default that a first subscription brings along.
    for name in ('aaa', 'bbb', 'ccc'):
        listed = 'on' if name == 'bbb' else ''
        await create(client, modhash, name=name, title=name.upper(), allow_top=listed)
    bob = (await register(other_client, 'bob'))['data']['modhash']
    await post_json(
        other_client, '/api/subscribe', {'uh': bob, 'action': 'sub', 'community_name': 'bbb'}
    )

    popular = (await client.get('/ins.json?limit=2')).json()['data']
    assert [c['data']['display_name'] for c in popular['children']] == ['bbb', 'ccc']
    assert popular['after'] == 't5_3'
    rest = (await client.get(f'/ins.json?after={popular["after"]}')).json()['data']
    assert [c['data']['display_name'] for c in rest['children']] == ['aaa']
    assert rest['after'] is None

    newest = (await client.get('/ins/new.json')).json()['data']['children']
    assert [c['data']['display_name'] for c in newest] == ['ccc', 'bbb', 'aaa']
    moderated = (await client.get('/ins/mine/moderator.json')).json()['data']['children']
    assert [c['data']['display_name'] for c in moderated] == ['aaa', 'bbb', 'ccc']

    page = await client.get('/ins/')
    assert page.status_code == 200
    assert 'AAA' in page.text
    assert (await other_client.get('/ins/mine', follow_redirects=False)).status_code == 200
