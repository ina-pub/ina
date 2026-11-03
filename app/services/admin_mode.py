"""Admin mode: a site admin's powers act only while it is on, as r2 had it.

Turning it on asks for the password again and sets a cookie for the browser session. The cookie
names the session it belongs to, so logging out ends it too. It lasts nine hours from being
turned on, and ends sooner after fifteen minutes unused: each request renews it, as r2's did.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import TYPE_CHECKING

from itsdangerous import BadSignature, URLSafeTimedSerializer

if TYPE_CHECKING:
    from app.services.sessions import Session
    from app.settings import Settings

COOKIE = 'ina_admin'
LIFETIME = timedelta(hours=9)
IDLE = timedelta(minutes=15)


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt='admin-mode')


def encode(settings: Settings, session: Session, started: float | None = None) -> str:
    """The cookie for ``session``, turned on at ``started`` (now, if not given)."""
    return _serializer(settings).dumps([session.id, time.time() if started is None else started])


def started(settings: Settings, cookie: str | None, session: Session | None) -> float | None:
    """When admin mode was turned on, if ``cookie`` keeps it on for ``session``; else None."""
    if not cookie or session is None:
        return None
    try:
        session_id, began = _serializer(settings).loads(cookie, max_age=int(IDLE.total_seconds()))
        began = float(began)
    except (BadSignature, ValueError, TypeError):
        return None
    if session_id != session.id or time.time() - began > LIFETIME.total_seconds():
        return None
    return began
