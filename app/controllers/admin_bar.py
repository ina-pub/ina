"""The admin bar: where each request's time went, shown in admin mode and when debugging.

The middleware records a request only when the bar will be shown: the site runs in debug, or
the request carries an admin mode cookie for its session. Nothing it records reaches an
edge-cacheable response. The page's bar fetches its request's recording from /admin/timings,
which only admins in admin mode (or anyone signed in, in debug) can read.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, cast

from litestar import Response, get
from litestar.enums import MediaType, ScopeType
from litestar.exceptions import NotFoundException
from litestar.params import FromPath
from redis.exceptions import RedisError

from app.controllers.deps import RedisDep, SettingsDep, UserDep
from app.controllers.render import NO_CACHE
from app.infra import timing
from app.infra.redis import key
from app.services import admin_mode, sessions

if TYPE_CHECKING:
    from collections.abc import Callable

    from litestar.types import ASGIApp, HTTPResponseStartEvent, Message, Receive, Scope, Send
    from redis.asyncio import Redis

    from app.settings import Settings

log = logging.getLogger(__name__)


def _cookies(scope: Scope) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, value in scope.get('headers', []):
        if name == b'cookie':
            for pair in value.decode('latin-1').split(';'):
                cookie, _, content = pair.strip().partition('=')
                found.setdefault(cookie, content.strip('"'))
    return found


def shows_bar(settings: Settings, scope: Scope) -> bool:
    """Whether this request may show the bar, judged before the app runs."""
    if settings.debug:
        return True
    cookies = _cookies(scope)
    session = sessions.decode(settings, cookies.get(sessions.COOKIE, ''))
    return admin_mode.started(settings, cookies.get(admin_mode.COOKIE), session) is not None


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes:
    return next((value for found, value in headers if found.lower() == name), b'')


def admin_bar_middleware(settings: Settings, redis: Redis) -> Callable[[ASGIApp], ASGIApp]:
    def middleware(app: ASGIApp) -> ASGIApp:
        async def handle(scope: Scope, receive: Receive, send: Send) -> None:
            if (
                scope['type'] != ScopeType.HTTP
                or scope['path'].startswith('/static/')
                or not shows_bar(settings, scope)
            ):
                await app(scope, receive, send)
                return
            recording = timing.Recording(uuid.uuid4().hex)
            token = timing.CURRENT.set(recording)

            async def sending(message: Message) -> None:
                if message['type'] == 'http.response.start':
                    message = await _finish(recording, message)
                await send(message)

            try:
                await app(scope, receive, sending)
            finally:
                timing.CURRENT.reset(token)

        return handle

    async def _finish(recording: timing.Recording, message: Message) -> Message:
        recording.finish()
        headers = list(cast('HTTPResponseStartEvent', message).get('headers', []))
        if b'public' in _header(headers, b'cache-control'):
            return message
        if _header(headers, b'content-type').startswith(b'text/html'):
            try:
                await redis.set(
                    key('timing', recording.id), recording.to_json(), ex=timing.KEPT_SECONDS
                )
            except RedisError:
                log.warning('could not keep timings for the admin bar', exc_info=True)
        headers.append((b'server-timing', recording.server_timing().encode()))
        return cast('HTTPResponseStartEvent', {**message, 'headers': headers})

    return middleware


@get('/admin/timings/{request_id:str}')
async def show_timings(
    request_id: FromPath[str], redis: RedisDep, settings: SettingsDep, user: UserDep
) -> Response[bytes]:
    """One page's recording, for its admin bar, while Redis keeps it."""
    if not (user.account.is_admin or settings.debug):
        raise NotFoundException
    found = await redis.get(key('timing', request_id))
    if found is None:
        raise NotFoundException
    content = found if isinstance(found, bytes) else found.encode()
    return Response(content=content, media_type=MediaType.JSON, headers={'Cache-Control': NO_CACHE})
