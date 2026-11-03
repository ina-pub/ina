from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlsplit

from app.controllers import auth, scopes
from app.infra.ratelimit import Bucket
from app.main import ROUTES
from tests.controllers.test_apps import make_app
from tests.controllers.test_comments import thread_with_link
from tests.helpers import PASSWORD, register

if TYPE_CHECKING:
    import httpx
    import pytest
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

BACK = 'https://reader.example/back'


def basic(client_id: str, secret: str = '') -> dict[str, str]:
    pair = base64.b64encode(f'{client_id}:{secret}'.encode()).decode()
    return {'Authorization': f'Basic {pair}'}


def authorize_url(client_id: str, /, **params: str) -> str:
    query = {
        'client_id': client_id,
        'response_type': 'code',
        'state': 'xyz',
        'redirect_uri': BACK,
        'duration': 'permanent',
        'scope': 'identity read',
        **params,
    }
    return '/api/v1/authorize?' + urlencode(query)


def sent_back(response: httpx.Response) -> dict[str, str]:
    """The parameters on the redirect back to the app."""
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers['location']).query)
    return {name: values[0] for name, values in query.items()}


async def allow(
    client: AsyncTestClient[Litestar], modhash: str, url: str, answer: str = 'yes'
) -> httpx.Response:
    """Answer the consent page the way its form does."""
    params = {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}
    return await client.post(
        '/api/v1/authorize',
        data={**params, 'uh': modhash, 'authorize': answer},
        follow_redirects=False,
    )


async def token(
    client: AsyncTestClient[Litestar], auth: dict[str, str], **form: str
) -> httpx.Response:
    return await client.post('/api/v1/access_token', data=form, headers=auth)


async def web_app(client: AsyncTestClient[Litestar], name: str = 'alice') -> tuple[str, str, str]:
    """A new account and its web app: the account's modhash, the app's id and its secret."""
    modhash: str = (await register(client, name))['data']['modhash']
    made = await make_app(client, modhash, redirect_uri=BACK)
    return modhash, made['data']['client_id'], made['data']['client_secret']


async def grant(
    client: AsyncTestClient[Litestar], name: str = 'alice', **params: str
) -> tuple[str, str, str, dict[str, Any]]:
    """A new account lets its web app in: its modhash, the app's id and secret, and the token."""
    alice, client_id, secret = await web_app(client, name)
    code = sent_back(await allow(client, alice, authorize_url(client_id, **params)))['code']
    issued = await token(
        client,
        basic(client_id, secret),
        grant_type='authorization_code',
        code=code,
        redirect_uri=BACK,
    )
    assert issued.status_code == 200, issued.text
    return alice, client_id, secret, issued.json()


async def test_let_an_app_in(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, client_id, secret = await web_app(client)
    url = authorize_url(client_id)

    page = await client.get(url)
    assert page.status_code == 200
    assert 'reader' in page.text
    assert 'your account name' in page.text
    assert 'reading' in page.text
    assert 'take it back' in page.text
    # The answer redirects to the app, so the page lets its form go there.
    assert "form-action 'self' https://reader.example" in page.headers['content-security-policy']
    # Logged out, the page asks for a login first and comes back with the request whole.
    away = await other_client.get(url, follow_redirects=False)
    assert parse_qs(urlsplit(away.headers['location']).query)['dest'] == [url]

    declined = sent_back(await allow(client, alice, url, answer='no'))
    assert declined == {'error': 'access_denied', 'state': 'xyz'}

    back = sent_back(await allow(client, alice, url))
    assert back['state'] == 'xyz'
    issued = await token(
        client,
        basic(client_id, secret),
        grant_type='authorization_code',
        code=back['code'],
        redirect_uri=BACK,
    )
    body = issued.json()
    assert issued.headers['cache-control'] == 'no-store'
    assert body['token_type'] == 'bearer'
    assert body['expires_in'] == 3600
    assert body['scope'] == 'identity read'
    assert body['access_token'] != body['refresh_token']

    # A code is good once.
    again = await token(
        client, basic(client_id, secret), grant_type='authorization_code', code=back['code']
    )
    assert (again.status_code, again.json()) == (400, {'error': 'invalid_grant'})

    apps = (await client.get('/prefs/apps')).text
    assert 'class="app-list authorized"' in apps
    assert 'take back its access' in apps
    await client.post('/api/revokeapp', data={'uh': alice, 'client_id': client_id})
    assert 'take back its access' not in (await client.get('/prefs/apps')).text
    refreshed = await token(
        client,
        basic(client_id, secret),
        grant_type='refresh_token',
        refresh_token=body['refresh_token'],
    )
    assert refreshed.json() == {'error': 'invalid_grant'}


async def test_refresh_and_revoke(client: AsyncTestClient[Litestar]) -> None:
    alice, client_id, secret, issued = await grant(client)
    auth = basic(client_id, secret)
    renewed = await token(
        client, auth, grant_type='refresh_token', refresh_token=issued['refresh_token']
    )
    assert renewed.status_code == 200
    fresh = renewed.json()
    assert fresh['scope'] == 'identity read'
    assert fresh['refresh_token'] != issued['refresh_token']
    # The old refresh token was replaced.
    stale = await token(
        client, auth, grant_type='refresh_token', refresh_token=issued['refresh_token']
    )
    assert stale.json() == {'error': 'invalid_grant'}

    revoked = await client.post(
        '/api/v1/revoke_token', data={'token': fresh['access_token']}, headers=auth
    )
    assert revoked.status_code == 204
    gone = await token(
        client, auth, grant_type='refresh_token', refresh_token=fresh['refresh_token']
    )
    assert gone.json() == {'error': 'invalid_grant'}
    # Revoking an unknown token is fine too.
    unknown = await client.post('/api/v1/revoke_token', data={'token': 'nope'}, headers=auth)
    assert unknown.status_code == 204

    # A temporary grant has no refresh token.
    _, _, temporary = await another_grant(client, alice, duration='temporary')
    assert 'refresh_token' not in temporary


async def another_grant(
    client: AsyncTestClient[Litestar], alice: str, **params: str
) -> tuple[str, str, dict[str, Any]]:
    """Another of alice's apps, let in with ``params``."""
    made = await make_app(client, alice, name='other', redirect_uri=BACK)
    client_id, secret = made['data']['client_id'], made['data']['client_secret']
    code = sent_back(await allow(client, alice, authorize_url(client_id, **params)))['code']
    issued = await token(
        client, basic(client_id, secret), grant_type='authorization_code', code=code
    )
    return client_id, secret, issued.json()


async def test_bad_requests(client: AsyncTestClient[Litestar]) -> None:
    alice, client_id, secret = await web_app(client)
    # Without a known app and its own redirect URI, nothing goes back to the app.
    for params in ({'client_id': 'nope'}, {'redirect_uri': 'https://evil.example/'}):
        page = await client.get(authorize_url(client_id, **params))
        assert page.status_code == 400
        assert "<h1>this app can't be let in</h1>" in page.text
    cases = (
        ({'scope': 'identity everything'}, 'invalid_scope'),
        ({'scope': ''}, 'invalid_scope'),
        ({'response_type': 'token'}, 'unsupported_response_type'),
        ({'duration': 'forever'}, 'invalid_request'),
        ({'code_challenge': 'abc', 'code_challenge_method': 'plain'}, 'invalid_request'),
    )
    for params, error in cases:
        back = sent_back(
            await client.get(authorize_url(client_id, **params), follow_redirects=False)
        )
        assert back == {'error': error, 'state': 'xyz'}, params

    wrong = await token(
        client, basic(client_id, 'wrong'), grant_type='client_credentials', scope='read'
    )
    assert wrong.status_code == 401
    assert wrong.json() == {'error': 'invalid_client'}
    assert wrong.headers['www-authenticate'] == 'Basic realm="ina"'
    odd = await token(client, basic(client_id, secret), grant_type='password')
    assert odd.json() == {'error': 'unsupported_grant_type'}

    code = sent_back(await allow(client, alice, authorize_url(client_id)))['code']
    elsewhere = await token(
        client,
        basic(client_id, secret),
        grant_type='authorization_code',
        code=code,
        redirect_uri='https://reader.example/other',
    )
    assert elsewhere.json() == {'error': 'invalid_grant'}


async def test_installed_apps_use_pkce(client: AsyncTestClient[Litestar]) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    back = 'com.example.reader:/back'
    made = await make_app(client, alice, app_type='installed', redirect_uri=back)
    client_id = made['data']['client_id']
    url = authorize_url(client_id, redirect_uri=back, duration='temporary')
    missing = sent_back(await client.get(url, follow_redirects=False))
    assert missing['error'] == 'invalid_request'

    verifier = 'a-long-random-string-the-app-keeps-to-itself-1234567890'
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=')
    url = authorize_url(
        client_id,
        redirect_uri=back,
        code_challenge=challenge.decode(),
        code_challenge_method='S256',
    )
    page = await client.get(url)
    assert "form-action 'self' com.example.reader:" in page.headers['content-security-policy']
    code = sent_back(await allow(client, alice, url))['code']
    # An installed app has no secret; the verifier proves it asked for the code.
    bad = await token(
        client, basic(client_id), grant_type='authorization_code', code=code, code_verifier='guess'
    )
    assert bad.json()['error'] == 'invalid_grant'
    code = sent_back(await allow(client, alice, url))['code']
    issued = await token(
        client,
        {},
        client_id=client_id,
        grant_type='authorization_code',
        code=code,
        code_verifier=verifier,
    )
    assert issued.status_code == 200
    # Without a secret, it can't act for itself.
    alone = await token(client, basic(client_id), grant_type='client_credentials', scope='read')
    assert alone.json() == {'error': 'unauthorized_client'}


async def test_client_credentials(client: AsyncTestClient[Litestar]) -> None:
    alice, client_id, secret = await web_app(client)
    reading = await token(
        client, basic(client_id, secret), grant_type='client_credentials', scope='read'
    )
    assert reading.json()['scope'] == 'read'
    assert 'refresh_token' not in reading.json()
    more = await token(
        client, basic(client_id, secret), grant_type='client_credentials', scope='vote'
    )
    assert more.json()['error'] == 'invalid_scope'

    script = await make_app(client, alice, app_type='script', name='bot')
    auth = basic(script['data']['client_id'], script['data']['client_secret'])
    acting = await token(client, auth, grant_type='client_credentials', scope='identity vote')
    assert acting.json()['scope'] == 'identity vote'


async def test_scopes(client: AsyncTestClient[Litestar]) -> None:
    every = (await client.get('/api/v1/scopes')).json()
    assert every['identity'] == {
        'id': 'identity',
        'name': 'your account name',
        'description': 'See your username and when you signed up.',
    }
    assert 'mycommunities' in every
    some = (await client.get('/api/v1/scopes?scopes=read,vote')).json()
    assert sorted(some) == ['read', 'vote']


def bearer(access_token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {access_token}'}


async def script_token(
    client: AsyncTestClient[Litestar], modhash: str, scope: str
) -> dict[str, str]:
    """A token for the account behind ``client``, from a script of its own."""
    made = await make_app(client, modhash, app_type='script', name='bot')
    auth = basic(made['data']['client_id'], made['data']['client_secret'])
    issued = await token(client, auth, grant_type='client_credentials', scope=scope)
    assert issued.status_code == 200, issued.text
    return bearer(issued.json()['access_token'])


async def test_apps_act_with_their_scopes(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice = await thread_with_link(client)
    _, _, _, issued = await grant(other_client, 'bob', scope='identity read vote subscribe')
    bob = bearer(issued['access_token'])

    me = await client.get('/api/v1/me', headers=bob)
    # The token is bob's; the cookie alice's browser also sends is ignored.
    assert me.json()['name'] == 'bob'
    assert me.json().get('modhash', '') == ''
    # Pages answer in JSON, and are never cached at the edge.
    front = await other_client.get('/', headers=bob)
    assert front.json()['kind'] == 'Listing'
    assert front.headers['cache-control'] == 'private, no-store'
    assert 'Authorization' in front.headers['vary']

    # No modhash is needed: a browser never sends a token on its own.
    voted = await other_client.post('/api/vote', data={'id': 't3_1', 'dir': '1'}, headers=bob)
    assert voted.status_code == 200, voted.text
    listing = (await other_client.get('/in/pics/', headers=bob)).json()
    assert listing['data']['children'][0]['data']['likes'] is True

    refused = await other_client.post('/api/save', data={'id': 't3_1'}, headers=bob)
    assert refused.status_code == 403
    assert refused.json() == {'error': 'insufficient_scope'}
    assert refused.headers['www-authenticate'] == (
        'Bearer realm="ina", error="insufficient_scope", scope="save"'
    )
    # Routes with no scope are closed to apps.
    closed = await other_client.post(
        '/api/update_email', data={'curpass': PASSWORD, 'email': 'b@example.org'}, headers=bob
    )
    assert closed.status_code == 403
    assert 'scope=' not in closed.headers['www-authenticate']

    # Some routes need a scope that depends on what they're asked.
    befriend = await other_client.post(
        '/api/friend', data={'type': 'friend', 'name': 'alice'}, headers=bob
    )
    assert befriend.status_code == 200, befriend.text
    ban = await other_client.post(
        '/api/friend', data={'type': 'banned', 'name': 'alice'}, headers=bob
    )
    assert ban.json() == {'error': 'insufficient_scope'}
    log = await other_client.get('/in/pics/about/log', headers=bob)
    assert log.headers['www-authenticate'].endswith('scope="modlog"')
    mine = await script_token(client, alice, 'modlog')
    assert (await client.get('/in/pics/about/log', headers=mine)).status_code == 200

    unknown = await client.get('/api/v1/me', headers=bearer('nope'))
    assert unknown.status_code == 401
    assert unknown.json() == {'error': 'invalid_token'}
    assert unknown.headers['www-authenticate'] == 'Bearer realm="ina", error="invalid_token"'


async def test_an_app_on_its_own_reads_logged_out(client: AsyncTestClient[Litestar]) -> None:
    alice = await thread_with_link(client)
    made = await make_app(client, alice, redirect_uri=BACK)
    auth = basic(made['data']['client_id'], made['data']['client_secret'])
    issued = await token(client, auth, grant_type='client_credentials', scope='read')
    app = bearer(issued.json()['access_token'])
    listing = (await client.get('/in/pics/', headers=app)).json()
    assert listing['data']['children'][0]['data']['likes'] is None
    assert (await client.get('/api/v1/me', headers=app)).status_code == 403


def test_routes_closed_to_apps() -> None:
    """Signing in, settings and form pages, gates and static files stay the browser's."""
    scoped = scopes.needed()
    closed = {
        f'{h.fn.__module__.rsplit(".", 1)[-1]}.{h.fn.__name__}' for h in ROUTES if h not in scoped
    }
    assert closed == {
        'accounts.login',
        'accounts.logout',
        'accounts.register',
        'accounts.request_password_reset',
        'accounts.reset_password',
        'accounts.show_login',
        'accounts.show_password',
        'accounts.show_reset_password',
        'admin.act_on_community',
        'admin.admin_off',
        'admin.admin_on',
        'admin.remove_all',
        'admin.show_admin',
        'admin_bar.show_timings',
        'admin.show_admin_on',
        'admin.suspend',
        'admin.unsuspend',
        'api_docs.show_api',
        'apps.delete_app',
        'apps.new_app_secret',
        'apps.revoke_app',
        'apps.show_apps',
        'apps.update_app',
        'assets.show_asset',
        'communities.show_community_create',
        'gates.answer_over18',
        'gates.answer_quarantine',
        'gates.show_over18',
        'gates.show_quarantine',
        'health.show_health',
        'listings.show_submit',
        'messages.show_compose',
        'multis.create_multi_from_form',
        'multis.edit_multi',
        'oauth.access_token',
        'oauth.answer_authorize',
        'oauth.revoke_token',
        'oauth.show_authorize',
        'prefs.show_prefs',
        'prefs.show_prefs_update',
        'prefs.update_email',
        'prefs.update_password',
        'prefs.update_prefs',
        'stylesheets.serve_media',
        'stylesheets.serve_stylesheet',
    }


async def test_apps_are_rate_limited(
    client: AsyncTestClient[Litestar], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, 'API_BY_APP', Bucket('api', capacity=3, period=60))
    alice: str = (await register(client, 'alice'))['data']['modhash']
    app = await script_token(client, alice, 'identity')
    first = await client.get('/api/v1/me', headers=app)
    assert first.headers['x-ratelimit-used'] == '1'
    assert first.headers['x-ratelimit-remaining'] == '2'
    assert first.headers['x-ratelimit-reset'] == '20'
    await client.get('/api/v1/me', headers=app)
    await client.get('/api/v1/me', headers=app)
    over = await client.get('/api/v1/me', headers=app)
    assert over.status_code == 429
    assert over.json()['json']['errors'][0][0] == 'RATELIMIT'
    assert over.headers['x-ratelimit-remaining'] == '0'
    assert int(over.headers['retry-after']) > 0
    # Browsers aren't counted.
    assert 'x-ratelimit-used' not in (await client.get('/api/v1/me')).headers
