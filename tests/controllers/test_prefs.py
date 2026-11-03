from __future__ import annotations

from typing import TYPE_CHECKING

from tests.helpers import PASSWORD, codes, me, post_json, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def test_prefs_need_a_login(client: AsyncTestClient[Litestar]) -> None:
    response = await client.get('/prefs', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login?dest=%2Fprefs'
    posted = await client.post('/post/options', data={'api_type': 'json'})
    assert posted.status_code == 403
    assert codes(posted.json()['json']) == [('USER_REQUIRED', None)]


async def test_update_options(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client))['data']['modhash']
    page = await client.get('/prefs')
    assert page.status_code == 200
    assert 'name="no_profanity"' in page.text

    form = {'uh': modhash, 'newwindow': 'on', 'numsites': '100', 'min_link_score': ''}
    response = await client.post('/post/options', data=form, follow_redirects=False)
    assert response.headers['location'] == '/prefs?done=true'
    prefs = (await client.get('/api/v1/me/prefs')).json()
    assert prefs['newwindow'] is True
    assert prefs['numsites'] == 100
    assert prefs['min_link_score'] is None
    assert prefs['no_profanity'] is False
    assert 'Saved.' in (await client.get('/prefs?done=true')).text


async def test_bad_option_is_reported(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client))['data']['modhash']
    form = {'uh': modhash, 'numsites': '7'}
    response = await client.post('/post/options', data=form, follow_redirects=False)
    assert response.headers['location'] == '/prefs?generic_error=INVALID_OPTION'
    assert (await client.get('/api/v1/me/prefs')).json()['numsites'] == 25


async def test_options_need_the_csrf_token(client: AsyncTestClient[Litestar]) -> None:
    await register(client)
    response = await client.post('/post/options', data={'newwindow': 'on'})
    assert response.status_code == 403
    assert (await client.get('/api/v1/me/prefs')).json()['newwindow'] is False


async def test_change_password_keeps_this_session_only(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    modhash = (await register(client))['data']['modhash']
    await post_json(other_client, '/api/login', {'user': 'alice', 'passwd': PASSWORD})

    wrong = await post_json(
        client,
        '/api/update_password',
        {'uh': modhash, 'curpass': 'nope', 'newpass': 'newpass1', 'verpass': 'newpass1'},
    )
    assert codes(wrong) == [('WRONG_PASSWORD', 'curpass')]

    body = await post_json(
        client,
        '/api/update_password',
        {'uh': modhash, 'curpass': PASSWORD, 'newpass': 'newpass1', 'verpass': 'newpass1'},
    )
    assert body['errors'] == []
    assert body['data']['modhash'] != modhash
    assert (await me(client)).status_code == 200
    assert (await me(other_client)).status_code == 403


async def test_change_email(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client, email='old@example.com'))['data']['modhash']
    bad = await post_json(
        client, '/api/update_email', {'uh': modhash, 'curpass': PASSWORD, 'email': 'nope'}
    )
    assert codes(bad) == [('BAD_EMAIL', 'email')]
    form = {'uh': modhash, 'curpass': PASSWORD, 'email': 'new@example.com'}
    response = await client.post('/api/update_email', data=form, follow_redirects=False)
    assert response.headers['location'] == '/prefs/update?done=email'
    page = await client.get('/prefs/update')
    assert 'value="new@example.com"' in page.text
