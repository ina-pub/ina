from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services import sessions
from tests.helpers import PASSWORD, codes, me, post_json, register, session_cookie

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def test_register_signs_in(client: AsyncTestClient[Litestar]) -> None:
    body = await register(client, 'alice')
    assert body['data']['modhash']
    assert body['data']['cookie'] == session_cookie(client)
    response = await me(client)
    assert response.status_code == 200
    assert response.json()['name'] == 'alice'
    assert response.json()['modhash'] == body['data']['modhash']


@pytest.mark.parametrize(
    ('form', 'expected'),
    [
        ({'user': 'al'}, [('USERNAME_TOO_SHORT', 'user')]),
        ({'user': 'a' * 21}, [('USERNAME_TOO_SHORT', 'user')]),
        ({'user': 'al ice'}, [('USERNAME_INVALID_CHARACTERS', 'user')]),
        ({'user': 'alicé'}, [('USERNAME_INVALID_CHARACTERS', 'user')]),
        ({'passwd': 'short', 'passwd2': 'short'}, [('SHORT_PASSWORD', 'passwd')]),
        ({'passwd2': 'different'}, [('BAD_PASSWORD_MATCH', 'passwd2')]),
        ({'email': 'nope'}, [('BAD_EMAIL', 'email')]),
        ({'user': 'a!', 'email': 'nope'}, [('USERNAME_TOO_SHORT', 'user'), ('BAD_EMAIL', 'email')]),
    ],
)
async def test_register_validation(
    client: AsyncTestClient[Litestar], form: dict[str, str], expected: list[tuple[str, str]]
) -> None:
    defaults = {'user': 'alice', 'passwd': PASSWORD, 'passwd2': PASSWORD, 'email': ''}
    body = await post_json(client, '/api/register', {**defaults, **form})
    assert codes(body) == expected
    assert session_cookie(client) is None


async def test_register_taken_name_ignores_case(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await register(client, 'alice')
    form = {'user': 'ALICE', 'passwd': PASSWORD, 'passwd2': PASSWORD}
    body = await post_json(other_client, '/api/register', form)
    assert codes(body) == [('USERNAME_TAKEN', 'user')]


async def test_register_without_js_redirects_to_dest(client: AsyncTestClient[Litestar]) -> None:
    form = {'user': 'alice', 'passwd': PASSWORD, 'passwd2': PASSWORD, 'dest': '/prefs'}
    response = await client.post('/post/reg', data=form, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/prefs'
    assert session_cookie(client)


async def test_register_without_js_shows_errors_on_the_page(
    client: AsyncTestClient[Litestar],
) -> None:
    form = {'user': 'al', 'passwd': PASSWORD, 'passwd2': 'nope', 'email': 'x@example.com'}
    response = await client.post('/post/reg', data=form)
    assert response.status_code == 200
    assert 'class="error USERNAME_TOO_SHORT field-user"' in response.text
    assert 'usernames are 3 to 20 characters long' in response.text
    assert 'value="x@example.com"' in response.text


@pytest.mark.parametrize('dest', ['//evil.example', 'https://evil.example', '/\\evil.example'])
async def test_dest_never_leaves_the_site(client: AsyncTestClient[Litestar], dest: str) -> None:
    form = {'user': 'alice', 'passwd': PASSWORD, 'passwd2': PASSWORD, 'dest': dest}
    response = await client.post('/post/reg', data=form, follow_redirects=False)
    assert response.headers['location'] == '/'


async def test_login(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    await register(client, 'alice')
    wrong = await post_json(other_client, '/api/login', {'user': 'alice', 'passwd': 'nope'})
    assert codes(wrong) == [('WRONG_PASSWORD', 'passwd')]
    unknown = await post_json(other_client, '/api/login', {'user': 'bob', 'passwd': PASSWORD})
    assert codes(unknown) == [('WRONG_PASSWORD', 'passwd')]
    body = await post_json(other_client, '/api/login', {'user': 'ALICE', 'passwd': PASSWORD})
    assert body['errors'] == []
    assert (await me(other_client)).json()['name'] == 'alice'


async def test_remember_me_sets_a_persistent_cookie(client: AsyncTestClient[Litestar]) -> None:
    await register(client, 'alice')
    response = await client.post(
        '/api/login', data={'user': 'alice', 'passwd': PASSWORD, 'rem': 'on', 'api_type': 'json'}
    )
    assert 'Max-Age=31536000' in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=lax' in response.headers['set-cookie']


async def test_login_is_rate_limited(client: AsyncTestClient[Litestar]) -> None:
    await register(client, 'alice')
    for _ in range(6):
        await post_json(client, '/api/login', {'user': 'alice', 'passwd': 'nope'})
    body = await post_json(client, '/api/login', {'user': 'alice', 'passwd': PASSWORD})
    assert codes(body) == [('RATELIMIT', 'ratelimit')]


async def test_logout_needs_the_csrf_token(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    refused = await client.post('/logout', data={'api_type': 'json'})
    assert refused.status_code == 403
    assert codes(refused.json()['json']) == [('BAD_CSRF', None)]
    wrong = await client.post('/logout', data={'uh': 'guess', 'api_type': 'json'})
    assert wrong.status_code == 403
    by_header = await client.post(
        '/logout', data={'api_type': 'json'}, headers={'X-Modhash': modhash}
    )
    assert by_header.status_code == 200


async def test_logout_revokes_the_session(client: AsyncTestClient[Litestar]) -> None:
    modhash = (await register(client, 'alice'))['data']['modhash']
    cookie = session_cookie(client)
    assert cookie is not None
    response = await client.post(
        '/logout', data={'uh': modhash, 'dest': '/user/alice'}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers['location'] == '/user/alice'
    assert session_cookie(client) is None
    # Replaying the old cookie does not bring the session back.
    client.cookies.set(sessions.COOKIE, cookie)
    assert (await me(client)).status_code == 403


async def test_login_replaces_the_previous_session(client: AsyncTestClient[Litestar]) -> None:
    await register(client, 'alice')
    first = session_cookie(client)
    assert first is not None
    await post_json(client, '/api/login', {'user': 'alice', 'passwd': PASSWORD})
    assert session_cookie(client) != first
    client.cookies.set(sessions.COOKIE, first)
    assert (await me(client)).status_code == 403


async def test_logged_in_pages_are_private(client: AsyncTestClient[Litestar]) -> None:
    public = await client.get('/login')
    assert public.headers['cache-control'] == 'public, max-age=60'
    assert 'Cookie' in public.headers['vary']
    await register(client, 'alice')
    private = await client.get('/login')
    assert private.headers['cache-control'] == 'private, no-store'
    assert "You're logged in as alice." in private.text
    assert '<span class="user"><a href="/user/alice/">alice</a>' in private.text


async def test_pages_without_json_are_404_as_json(client: AsyncTestClient[Litestar]) -> None:
    response = await client.get('/login.json')
    assert response.status_code == 404
    assert codes(response.json()['json']) == [('NOT_FOUND', None)]
