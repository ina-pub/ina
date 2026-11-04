from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import SecretStr

from app.repos.base import Account, Prefs
from app.services import sessions
from app.settings import Settings

if TYPE_CHECKING:
    import pytest

ACCOUNT = Account(
    id=7,
    name='alice',
    password_hash='x',
    email=None,
    email_verified=False,
    created=datetime(2026, 11, 1, tzinfo=UTC),
    link_karma=1,
    comment_karma=0,
    prefs=Prefs(),
    deleted=False,
    suspended=False,
    in_timeout=False,
    inbox_count=0,
    session_version=3,
)


def test_cookie_round_trips(settings: Settings) -> None:
    session = sessions.start(ACCOUNT, remember=True)
    decoded = sessions.decode(settings, sessions.encode(settings, session))
    assert decoded is not None
    assert (decoded.account_id, decoded.id, decoded.version, decoded.csrf, decoded.remember) == (
        7,
        session.id,
        3,
        session.csrf,
        True,
    )


def test_tampered_cookie_is_rejected(settings: Settings) -> None:
    cookie = sessions.encode(settings, sessions.start(ACCOUNT, remember=False))
    assert sessions.decode(settings, cookie[:-2] + 'xx') is None
    assert sessions.decode(settings, 'not-a-cookie') is None


def test_cookie_signed_with_another_key_is_rejected(settings: Settings) -> None:
    other = Settings(secret_key=SecretStr('a-different-key'))
    cookie = sessions.encode(other, sessions.start(ACCOUNT, remember=False))
    assert sessions.decode(settings, cookie) is None


def test_browser_session_expires_before_a_remembered_one(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    short = sessions.encode(settings, sessions.start(ACCOUNT, remember=False))
    long = sessions.encode(settings, sessions.start(ACCOUNT, remember=True))
    later = time.time() + sessions.BROWSER_AGE.total_seconds() + 60
    monkeypatch.setattr(time, 'time', lambda: later)
    assert sessions.decode(settings, short) is None
    assert sessions.decode(settings, long) is not None
