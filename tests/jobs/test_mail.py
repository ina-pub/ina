from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.ids import Fullname, Kind
from app.infra import mail as mail_infra
from app.jobs import mail
from tests.helpers import register

if TYPE_CHECKING:
    from email.message import EmailMessage

    from litestar import Litestar
    from litestar.testing import AsyncTestClient

    from app.settings import Settings


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    sent: list[EmailMessage] = []

    async def capture(_: Settings, message: EmailMessage) -> None:
        sent.append(message)

    monkeypatch.setattr(mail_infra, 'send', capture)
    return sent


async def test_reset_email_links_to_the_reset_page(
    client: AsyncTestClient[Litestar], outbox: list[EmailMessage]
) -> None:
    await register(client, 'alice', email='alice@example.com')
    await mail.password_reset(str(Fullname(Kind.ACCOUNT, 1)))
    (message,) = outbox
    assert message['To'] == 'alice@example.com'
    body = message.get_content()
    assert 'Hi alice,' in body
    link = next(line.strip() for line in body.splitlines() if '/resetpassword/' in line)
    token = link.rsplit('/', 1)[1]
    assert (await client.get(f'/resetpassword/{token}')).status_code == 200


async def test_missing_account_sends_nothing(outbox: list[EmailMessage]) -> None:
    await mail.password_reset(str(Fullname(Kind.ACCOUNT, 404)))
    assert outbox == []
