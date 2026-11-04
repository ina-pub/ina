"""Who is asking: the session cookie resolved to an account, the CSRF check, and the client's IP."""

from __future__ import annotations

import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from litestar import Request
from litestar.connection import ASGIConnection
from litestar.datastructures import Cookie
from litestar.handlers import BaseRouteHandler
from litestar.status_codes import HTTP_403_FORBIDDEN, HTTP_429_TOO_MANY_REQUESTS

from app.controllers import consent
from app.controllers.deps import (
    AccountsDep,
    CacheDep,
    CommunitiesDep,
    LinksDep,
    MessagesDep,
    RedisDep,
    SettingsDep,
    SubscriptionsDep,
    ViewerDep,
    provide_accounts,
    provide_apps,
)
from app.controllers.render import EXTRA_HEADERS, WANTS_JSON, wants_json
from app.errors import ErrorCode, InaError
from app.infra import ratelimit
from app.infra.db import DB_SESSION
from app.repos.base import ModPermission
from app.services import admin_mode, oauth, sessions, traffic
from app.services.listings import bar_names
from app.services.oauth import Bearer, OAuthError, Problem
from app.services.ratelimits import API_BY_APP, wait_text
from app.services.sessions import Authenticated, Session
from app.settings import Settings
from app.views.base import Viewer

if TYPE_CHECKING:
    from redis.asyncio import Redis

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
# Put on a route's ``opt`` to skip the CSRF check, as r2 did for logged-out forms.
CSRF_EXEMPT = 'csrf_exempt'
# The OAuth scope a route needs, put on its ``opt`` from app.controllers.scopes. A route without
# one is closed to API apps; ANY_TOKEN lets in any token.
SCOPE = 'oauth_scope'
ANY_TOKEN = ''
# A scope name, or a function of the request that picks one.
type Needed = str | Callable[[Request[Any, Any, Any]], Awaitable[str]]
# An API app's checked token, kept on the request.
BEARER = 'bearer'
# Put on a route's ``opt`` to let an account in a timeout use it: reading, saving, its own
# settings and deleting what it posted. Everything else that changes something is refused.
WHILE_SUSPENDED = 'while_suspended'
# The renewed admin mode cookie, sent with whatever response the request gets.
RENEW_ADMIN = 'set-cookie'
VIEWER = 'viewer'
# The community names across the top of an HTML page, kept on the request for its template.
COMMUNITY_BAR = 'community_bar'


async def bearer_guard(
    connection: ASGIConnection[Any, Any, Any, Any], handler: BaseRouteHandler
) -> None:
    """Check an API app's token, and that it holds the scope the route needs.

    A request with a token is the app's alone: any session cookie is ignored, and it is
    answered in JSON.
    """
    scheme, _, token = connection.headers.get('authorization', '').partition(' ')
    if not isinstance(connection, Request) or scheme.lower() != 'bearer':
        return
    db_session = connection.scope['state'][DB_SESSION]
    grant = await oauth.bearer(
        provide_apps(db_session), provide_accounts(db_session), token.strip()
    )
    needed: Needed | None = handler.opt.get(SCOPE)
    if callable(needed):
        needed = await needed(connection)
    if needed is None or (needed != ANY_TOKEN and needed not in grant.token.scopes):
        error = OAuthError(Problem.INSUFFICIENT_SCOPE, status_code=HTTP_403_FORBIDDEN)
        error.scope = needed or ''
        raise error
    connection.scope['state'][BEARER] = grant
    connection.scope['state'][WANTS_JSON] = True
    await limit(connection, grant)


async def limit(connection: ASGIConnection[Any, Any, Any, Any], grant: Bearer) -> None:
    """Count the request against the app's limit, and say where it stands, as r2 did."""
    subject = f'{grant.token.client_id}:{grant.token.account_id or ""}'
    redis: Redis = connection.app.state.redis
    taken = await ratelimit.take(redis, API_BY_APP, subject)
    headers = {
        'X-Ratelimit-Used': str(API_BY_APP.capacity - taken.remaining),
        'X-Ratelimit-Remaining': str(taken.remaining),
        'X-Ratelimit-Reset': str(math.ceil(taken.refilled_in)),
    }
    if not taken.allowed:
        headers['Retry-After'] = str(math.ceil(taken.retry_after))
    connection.scope['state'].setdefault(EXTRA_HEADERS, {}).update(headers)
    if not taken.allowed:
        raise InaError.of(
            ErrorCode.RATELIMIT,
            'ratelimit',
            status_code=HTTP_429_TOO_MANY_REQUESTS,
            time=wait_text(taken.retry_after),
        )


async def provide_viewer(
    request: Request[Any, Any, Any],
    settings: SettingsDep,
    accounts: AccountsDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    links: LinksDep,
    messages: MessagesDep,
    redis: RedisDep,
    cache: CacheDep,
) -> Authenticated | None:
    grant: Bearer | None = request.scope['state'].get(BEARER)
    if grant is not None:
        account = grant.account
        # An app never acts with an admin's powers.
        if account is not None:
            account = replace(account, is_admin=False)
        viewer = Authenticated(account, None, grant=grant.token) if account else None
    else:
        cookie = request.cookies.get(sessions.COOKIE)
        viewer = await sessions.resolve(settings, cookie, accounts, redis)
        if viewer is not None:
            viewer = with_admin_mode(settings, request, viewer)
    if viewer is not None:
        # Moderators with mail access get a "mod messages" link, lit when modmail is new.
        mail = tuple(await communities.moderated_ids(viewer.account.id, needing=ModPermission.MAIL))
        if mail:
            new = await messages.modmail_since(list(mail), viewer.account.modmsgtime)
            viewer = replace(viewer, mail_communities=mail, has_mod_mail=new)
    # Error pages are rendered outside DI and read the viewer from here.
    request.state[VIEWER] = viewer
    if request.method == 'GET' and not wants_json(request):
        account = viewer.account if viewer else None
        request.state[COMMUNITY_BAR] = await bar_names(
            settings, communities, subscriptions, cache, account
        )
    # Every community page asks for the viewer, so this is where NSFW and quarantine stop it,
    # and where its traffic is counted.
    community = await consent.check(request, viewer, communities, subscriptions, links)
    if community is not None and not wants_json(request):
        who = traffic.visitor(
            viewer.account.id if viewer else None,
            client_ip(request),
            request.headers.get('user-agent', ''),
        )
        await traffic.record_view(redis, community.id, who)
    return viewer


def with_admin_mode(
    settings: Settings, request: Request[Any, Any, Any], viewer: Authenticated
) -> Authenticated:
    """An admin's account is one only while admin mode is on; using it renews the cookie."""
    if not viewer.account.is_admin:
        return viewer
    cookie = request.cookies.get(admin_mode.COOKIE)
    began = admin_mode.started(settings, cookie, viewer.session)
    if began is None or viewer.session is None:
        return replace(viewer, account=replace(viewer.account, is_admin=False), may_admin=True)
    renewed = admin_cookie(settings, admin_mode.encode(settings, viewer.session, began))
    request.scope['state'].setdefault(EXTRA_HEADERS, {})[RENEW_ADMIN] = renewed
    return replace(viewer, may_admin=True)


def admin_cookie(settings: Settings, value: str, *, expire: bool = False) -> str:
    """The Set-Cookie value for admin mode; it lasts as long as the browser session."""
    cookie = Cookie(
        key=admin_mode.COOKIE,
        value='' if expire else value,
        max_age=0 if expire else None,
        httponly=True,
        secure=settings.secure_cookies,
        samesite='strict',
    )
    return cookie.to_encoded_header()[1].decode()


async def provide_user(request: Request[Any, Any, Any], viewer: ViewerDep) -> Authenticated:
    if viewer is None:
        raise InaError.of(ErrorCode.USER_REQUIRED, status_code=HTTP_403_FORBIDDEN)
    account = viewer.account
    acting = request.method not in SAFE_METHODS
    if account.in_timeout and acting and not request.route_handler.opt.get(WHILE_SUSPENDED):
        expires = account.timeout_expires
        until = expires.strftime('%Y-%m-%d %H:%M UTC') if expires else 'an admin lifts it'
        raise InaError.of(ErrorCode.IN_TIMEOUT, status_code=HTTP_403_FORBIDDEN, until=until)
    return viewer


def to_viewer(viewer: Authenticated | None) -> Viewer | None:
    if viewer is None:
        return None
    account = viewer.account
    return Viewer(
        name=account.name,
        link_karma=account.link_karma,
        inbox_count=account.inbox_count,
        modhash=viewer.modhash,
        is_moderator=bool(viewer.mail_communities),
        has_mod_mail=viewer.has_mod_mail,
        show_stylesheets=account.prefs.show_stylesheets,
        may_admin=viewer.may_admin,
        admin_mode=account.is_admin,
    )


def viewer_from_state(request: Request[Any, Any, Any]) -> Viewer | None:
    viewer: Authenticated | None = request.state.get(VIEWER)
    return to_viewer(viewer)


def session_cookie(settings: Settings, session: Session) -> Cookie:
    return Cookie(
        key=sessions.COOKIE,
        value=sessions.encode(settings, session),
        max_age=int(session.max_age.total_seconds()) if session.remember else None,
        httponly=True,
        secure=settings.secure_cookies,
        samesite='lax',
    )


def expired_session_cookie(settings: Settings) -> Cookie:
    return Cookie(
        key=sessions.COOKIE,
        value='',
        max_age=0,
        httponly=True,
        secure=settings.secure_cookies,
        samesite='lax',
    )


async def csrf_guard(
    connection: ASGIConnection[Any, Any, Any, Any], handler: BaseRouteHandler
) -> None:
    """Check ``uh`` (or ``X-Modhash``) against the session's token on every unsafe request.

    A request without a session cookie carries no ambient credentials, so there is nothing to
    forge; those requests fail later with USER_REQUIRED if they need an account.
    """
    if not isinstance(connection, Request):
        return
    if connection.method in SAFE_METHODS or handler.opt.get(CSRF_EXEMPT):
        return
    # An app's token is sent on purpose, never by a browser on its own.
    if BEARER in connection.scope['state']:
        return
    cookie = connection.cookies.get(sessions.COOKIE)
    if not cookie:
        return
    session = sessions.decode(connection.app.state.settings, cookie)
    if session is None:
        return
    sent = connection.headers.get('x-modhash')
    if sent is None:
        sent = (await connection.form()).get('uh')
    if not isinstance(sent, str) or not secrets.compare_digest(sent, session.csrf):
        raise InaError.of(ErrorCode.BAD_CSRF, status_code=HTTP_403_FORBIDDEN)


def client_ip(request: Request[Any, Any, Any]) -> str:
    return request.client.host if request.client else 'unknown'


def local_path(dest: str | None, default: str = '/') -> str:
    """Only follow ``dest`` to a path on this site, never to another host."""
    if not dest or not dest.startswith('/') or dest.startswith('//') or '\\' in dest:
        return default
    return dest
