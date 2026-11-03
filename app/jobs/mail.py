"""The ``mail`` queue: emails sent on behalf of a request."""

from __future__ import annotations

import dramatiq

from app.ids import Fullname
from app.infra import mail
from app.jobs.context import transaction
from app.repos.pg.accounts import PgAccountRepo
from app.services import accounts
from app.settings import get_settings

RESET_BODY = """\
Hi {name},

Someone asked ina for a link to reset your password. If that was you, choose a new password here:

    {link}

The link works for a little while, once. If it wasn't you, there's nothing to do: your password
stays as it is, and asking for a link doesn't let anyone into your account.
"""


async def password_reset(account_fullname: str) -> None:
    settings = get_settings()
    async with transaction() as session:
        account = await PgAccountRepo(session).by_id(Fullname.parse(account_fullname).id)
    if account is None or account.email is None:
        return
    link = f'{settings.site_url}/resetpassword/{accounts.reset_token(settings, account)}'
    message = mail.compose(
        settings,
        to=account.email,
        subject='your ina password',
        body=RESET_BODY.format(name=account.name, link=link),
    )
    await mail.send(settings, message)


# The actor wraps a plain coroutine so tests can await it without a worker's event loop.
send_password_reset = dramatiq.actor(actor_name='send_password_reset', queue_name='mail')(
    password_reset
)
