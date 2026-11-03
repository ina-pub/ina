from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tests.helpers import codes, post_json, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


async def make_app(client: AsyncTestClient[Litestar], modhash: str, **form: str) -> dict[str, Any]:
    fields = {
        'app_type': 'web',
        'name': 'reader',
        'redirect_uri': 'https://reader.example/back',
        **form,
    }
    return await post_json(client, '/api/updateapp', {'uh': modhash, **fields})


async def test_register_an_app(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    made = await make_app(client, alice, about_url='reader.example', description='reads things')
    assert made['errors'] == []
    client_id, secret = made['data']['client_id'], made['data']['client_secret']
    assert len(secret) > 40
    page = (await client.get('/prefs/apps/')).text
    assert client_id in page
    assert 'reads things' in page
    # The secret is shown once, on the page that answers the form, and never again.
    assert secret not in page
    shown = await client.post(
        '/api/updateapp',
        data={
            'uh': alice,
            'app_type': 'web',
            'name': 'second',
            'redirect_uri': 'https://s.example',
        },
    )
    assert shown.status_code == 200
    assert 'class="app-secret"' in shown.text

    renewed = await post_json(client, '/api/new_app_secret', {'uh': alice, 'client_id': client_id})
    assert renewed['data']['client_secret'] not in {secret, None}

    # Installed apps keep no secret.
    installed = await make_app(
        client, alice, app_type='installed', redirect_uri='com.example.reader:/back'
    )
    assert installed['data']['client_secret'] is None
    refused = await client.post(
        '/api/new_app_secret',
        data={'uh': alice, 'client_id': installed['data']['client_id'], 'api_type': 'json'},
    )
    assert codes(refused.json()['json']) == [('INVALID_OPTION', 'client_id')]

    changed = await make_app(
        client, alice, client_id=client_id, name='reader two', app_type='installed'
    )
    assert changed['errors'] == []
    assert 'reader two' in (await client.get('/prefs/apps')).text

    # Only the owner sees or changes an app.
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    assert client_id not in (await other_client.get('/prefs/apps')).text
    stolen = await other_client.post(
        '/api/deleteapp', data={'uh': bob, 'client_id': client_id, 'api_type': 'json'}
    )
    assert stolen.status_code == 404

    await post_json(client, '/api/deleteapp', {'uh': alice, 'client_id': client_id})
    assert client_id not in (await client.get('/prefs/apps')).text


async def test_app_details_are_checked(client: AsyncTestClient[Litestar]) -> None:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    cases = (
        ({'name': ''}, ('NO_TEXT', 'name')),
        ({'name': 'x' * 101}, ('TOO_LONG', 'name')),
        ({'app_type': 'robot'}, ('INVALID_OPTION', 'app_type')),
        ({'redirect_uri': ''}, ('NO_URL', 'redirect_uri')),
        ({'redirect_uri': 'http://reader.example/back'}, ('BAD_URL', 'redirect_uri')),
        ({'redirect_uri': 'https://reader.example/#back'}, ('BAD_URL', 'redirect_uri')),
        ({'redirect_uri': 'com.example.reader:/back'}, ('BAD_URL', 'redirect_uri')),
        ({'about_url': 'not a link'}, ('BAD_URL', 'about_url')),
    )
    for form, error in cases:
        assert codes(await make_app(client, alice, **form)) == [error], form
    for uri in ('http://localhost:8080/back', 'http://127.0.0.1/back'):
        assert (await make_app(client, alice, redirect_uri=uri))['errors'] == []

    # Without JS, the form comes back with its error.
    page = await client.post(
        '/api/updateapp', data={'uh': alice, 'app_type': 'web', 'name': '', 'redirect_uri': 'x'}
    )
    assert page.status_code == 200
    assert 'field-name' in page.text
