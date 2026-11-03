"""Site sessions: a signed cookie naming the account, checked against revocations and the account.

The cookie carries the account id, a random session id, the account's session version, the CSRF
token (``uh``) and whether to remember the session. Bumping the account's session version signs
out every session; revoking a session id signs out one.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.infra import revoked

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.repos.base import Account, AccountRepo, OAuthToken
    from app.settings import Settings

COOKIE = 'ina_session'
REMEMBERED_AGE = timedelta(days=365)
# A session without "remember me" lasts until the browser closes, or this long at most.
BROWSER_AGE = timedelta(days=2)


@dataclass(frozen=True, slots=True, kw_only=True)
class Session:
    account_id: int
    id: str
    version: int
    csrf: str
    remember: bool
    issued: float

    @property
    def max_age(self) -> timedelta:
        return REMEMBERED_AGE if self.remember else BROWSER_AGE

    @property
    def expires_at(self) -> float:
        return self.issued + self.max_age.total_seconds()


@dataclass(frozen=True, slots=True)
class Authenticated:
    """The account a request acts for: by its session cookie, or by an API app's token."""

    account: Account
    # None when an app asks with a token; the token is ``grant``.
    session: Session | None
    # Communities whose modmail the account reads, and whether any came since it last looked.
    mail_communities: tuple[int, ...] = ()
    has_mod_mail: bool = False
    grant: OAuthToken | None = None
    # A site admin, whether or not admin mode is on; ``account.is_admin`` says whether it is.
    may_admin: bool = False

    @property
    def modhash(self) -> str:
        """The CSRF token forms send back; a token's requests have none and need none."""
        return self.session.csrf if self.session else ''


def start(account: Account, *, remember: bool, version: int | None = None) -> Session:
    return Session(
        account_id=account.id,
        id=secrets.token_urlsafe(16),
        version=account.session_version if version is None else version,
        csrf=secrets.token_urlsafe(24),
        remember=remember,
        issued=time.time(),
    )


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt='session')


def encode(settings: Settings, session: Session) -> str:
    payload = [session.account_id, session.id, session.version, session.csrf, session.remember]
    return _serializer(settings).dumps(payload)


def decode(settings: Settings, cookie: str) -> Session | None:
    try:
        payload, signed_at = _serializer(settings).loads(
            cookie, max_age=int(REMEMBERED_AGE.total_seconds()), return_timestamp=True
        )
        account_id, session_id, version, csrf, remember = payload
    except (BadSignature, ValueError, TypeError):
        return None
    session = Session(
        account_id=int(account_id),
        id=str(session_id),
        version=int(version),
        csrf=str(csrf),
        remember=bool(remember),
        issued=signed_at.timestamp(),
    )
    return session if session.expires_at > time.time() else None


async def resolve(
    settings: Settings, cookie: str | None, accounts: AccountRepo, redis: Redis
) -> Authenticated | None:
    """The signed-in account for a session cookie, or None if it is missing or no longer valid."""
    session = decode(settings, cookie) if cookie else None
    if session is None or await revoked.is_revoked(redis, session.id):
        return None
    account = await accounts.by_id(session.account_id)
    if (
        account is None
        or account.deleted
        or account.suspended
        or account.session_version != session.version
    ):
        return None
    return Authenticated(account, session)


async def end(redis: Redis, session: Session) -> None:
    await revoked.revoke(redis, session.id, expires_at=session.expires_at)
