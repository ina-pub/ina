from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.ids import Fullname
from app.jobs import mail
from app.services import accounts
from tests.helpers import PASSWORD, codes, find_account, me, post_json, register, session_cookie

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Password reset messages published to the mail queue, instead of RabbitMQ."""
    sent: list[str] = []
    monkeypatch.setattr(mail.send_password_reset, 'send', sent.append)
    return sent


async def test_request_queues_a_reset_email(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar], queued: list[str]
) -> None:
    await register(client, 'alice', email='alice@example.com')
    response = await other_client.post(
        '/api/password', data={'name': 'Alice'}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers['location'] == '/password?sent=true'
    assert [Fullname.parse(fullname).id for fullname in queued] == [1]


@pytest.mark.parametrize(
    ('name', 'code'),
    [('', 'NO_USER'), ('bob', 'USER_DOESNT_EXIST'), ('noemail', 'NO_EMAIL_FOR_USER')],
)
async def test_request_errors(
    client: AsyncTestClient[Litestar],
    other_client: AsyncTestClient[Litestar],
    queued: list[str],
    name: str,
    code: str,
) -> None:
    await register(client, 'noemail')
    body = await post_json(other_client, '/api/password', {'name': name})
    assert codes(body) == [(code, 'name')]
    assert queued == []


async def test_reset_changes_the_password_once(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar], settings: Settings
) -> None:
    await register(client, 'alice', email='alice@example.com')
    token = accounts.reset_token(settings, await find_account(settings, 'alice'))

    page = await other_client.get(f'/resetpassword/{token}')
    assert page.status_code == 200
    assert f'value="{token}"' in page.text
    assert page.headers['cache-control'] == 'private, no-store'

    mismatch = await post_json(
        other_client,
        '/api/resetpassword',
        {'key': token, 'passwd': 'newpass1', 'passwd2': 'newpass2'},
    )
    assert codes(mismatch) == [('BAD_PASSWORD_MATCH', 'passwd2')]

    form = {'key': token, 'passwd': 'newpass1', 'passwd2': 'newpass1'}
    done = await other_client.post('/api/resetpassword', data=form, follow_redirects=False)
    assert done.headers['location'] == '/login'

    # Every session was signed out, and the old password no longer works.
    assert (await me(client)).status_code == 403
    old = await post_json(other_client, '/api/login', {'user': 'alice', 'passwd': PASSWORD})
    assert codes(old) == [('WRONG_PASSWORD', 'passwd')]
    new = await post_json(other_client, '/api/login', {'user': 'alice', 'passwd': 'newpass1'})
    assert new['errors'] == []

    # The token was single-use.
    again = await other_client.get(f'/resetpassword/{token}', follow_redirects=False)
    assert again.headers['location'] == '/password?expired=true'
    reused = await other_client.post('/api/resetpassword', data=form, follow_redirects=False)
    assert reused.headers['location'] == '/password?expired=true'


async def test_forged_token_is_expired(client: AsyncTestClient[Litestar]) -> None:
    response = await client.get('/resetpassword/not-a-token', follow_redirects=False)
    assert response.headers['location'] == '/password?expired=true'
    assert session_cookie(client) is None
