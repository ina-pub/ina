"""Login, registration, logout and password reset.

Each form posts to ``/api/...`` (r2's JS endpoints) and also answers without JS, so the old
``/post/login`` and ``/post/reg`` paths are the same handlers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from litestar import Request, Response, get, post
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.response import Redirect
from redis.asyncio import Redis

from app.controllers.auth import (
    CSRF_EXEMPT,
    client_ip,
    expired_session_cookie,
    local_path,
    session_cookie,
    to_viewer,
)
from app.controllers.deps import AccountsDep, PasswordsDep, RedisDep, SettingsDep, ViewerDep
from app.controllers.render import Rendered
from app.errors import ErrorCode, InaError
from app.jobs.mail import send_password_reset
from app.repos.base import Account
from app.services import accounts as accounts_service
from app.services import ratelimits, sessions
from app.services.sessions import Authenticated
from app.settings import Settings
from app.views.accounts import LoginPage, PasswordPage, ResetPasswordPage
from app.views.forms import FormResult


@dataclass
class LoginForm:
    user: str = ''
    passwd: str = ''
    rem: str = ''
    dest: str = ''


@dataclass
class RegisterForm:
    user: str = ''
    passwd: str = ''
    passwd2: str = ''
    email: str = ''
    rem: str = ''
    dest: str = ''


@dataclass
class LogoutForm:
    dest: str = ''


@dataclass
class PasswordForm:
    name: str = ''


@dataclass
class ResetPasswordForm:
    key: str = ''
    passwd: str = ''
    passwd2: str = ''


def checked(value: str) -> bool:
    return value not in {'', 'off', 'false'}


async def signed_in(
    settings: Settings,
    redis: Redis,
    viewer: Authenticated | None,
    account: Account,
    *,
    remember: bool,
    dest: str,
) -> Rendered:
    """Replace any current session with a new one for ``account``."""
    if viewer is not None and viewer.session is not None:
        await sessions.end(redis, viewer.session)
    session = sessions.start(account, remember=remember)
    cookie = session_cookie(settings, session)
    result = FormResult(
        data={'modhash': session.csrf, 'cookie': cookie.value, 'need_https': False}, redirect=dest
    )
    return Rendered(content=result, cookies=[cookie])


@get(['/login', '/register'])
async def show_login(viewer: ViewerDep, dest: FromQuery[str] = '/') -> LoginPage:
    return LoginPage(viewer=to_viewer(viewer), dest=local_path(dest))


@post(['/api/login', '/post/login'], opt={CSRF_EXEMPT: True})
async def login(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[LoginForm],
    settings: SettingsDep,
    accounts: AccountsDep,
    passwords: PasswordsDep,
    redis: RedisDep,
    viewer: ViewerDep,
) -> Rendered:
    dest = local_path(data.dest)
    try:
        await ratelimits.enforce(redis, ratelimits.LOGIN_BY_IP, client_ip(request))
        await ratelimits.enforce(redis, ratelimits.LOGIN_BY_NAME, data.user)
        account = await accounts_service.authenticate(
            accounts, passwords, name=data.user, password=data.passwd
        )
    except InaError as error:
        page = LoginPage(
            viewer=to_viewer(viewer), dest=dest, user=data.user, login_errors=error.errors
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    return await signed_in(settings, redis, viewer, account, remember=checked(data.rem), dest=dest)


@post(['/api/register', '/post/reg'], opt={CSRF_EXEMPT: True})
async def register(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[RegisterForm],
    settings: SettingsDep,
    accounts: AccountsDep,
    passwords: PasswordsDep,
    redis: RedisDep,
    viewer: ViewerDep,
) -> Rendered:
    dest = local_path(data.dest)
    try:
        await ratelimits.enforce(redis, ratelimits.REGISTER_BY_IP, client_ip(request))
        account = await accounts_service.register(
            accounts,
            passwords,
            name=data.user,
            password=data.passwd,
            verify=data.passwd2,
            email=data.email,
        )
    except InaError as error:
        page = LoginPage(
            viewer=to_viewer(viewer),
            dest=dest,
            reg_user=data.user,
            reg_email=data.email,
            reg_errors=error.errors,
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    return await signed_in(settings, redis, viewer, account, remember=checked(data.rem), dest=dest)


@post('/logout')
async def logout(
    data: URLEncodedBody[LogoutForm], settings: SettingsDep, redis: RedisDep, viewer: ViewerDep
) -> Rendered:
    if viewer is not None and viewer.session is not None:
        await sessions.end(redis, viewer.session)
    result = FormResult(redirect=local_path(data.dest))
    return Rendered(content=result, cookies=[expired_session_cookie(settings)])


@get('/password')
async def show_password(
    viewer: ViewerDep, expired: FromQuery[str] = '', sent: FromQuery[str] = ''
) -> PasswordPage:
    return PasswordPage(viewer=to_viewer(viewer), expired=expired == 'true', sent=sent == 'true')


@post('/api/password', opt={CSRF_EXEMPT: True})
async def request_password_reset(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[PasswordForm],
    accounts: AccountsDep,
    redis: RedisDep,
    viewer: ViewerDep,
) -> Rendered:
    try:
        await ratelimits.enforce(redis, ratelimits.PASSWORD_RESET_BY_IP, client_ip(request))
        account = await accounts_service.account_for_reset_request(accounts, data.name)
    except InaError as error:
        page = PasswordPage(viewer=to_viewer(viewer), name=data.name, errors=error.errors)
        return Rendered(content=FormResult(errors=error.errors, page=page))
    # Publishing is a blocking network call.
    await asyncio.to_thread(send_password_reset.send, str(account.fullname))
    return Rendered(content=FormResult(redirect='/password?sent=true'))


@get('/resetpassword/{key:str}')
async def show_reset_password(
    key: FromPath[str], settings: SettingsDep, accounts: AccountsDep, viewer: ViewerDep
) -> Response[Any]:
    if await accounts_service.account_for_reset(settings, accounts, key) is None:
        return Redirect('/password?expired=true')
    return Rendered(content=ResetPasswordPage(viewer=to_viewer(viewer), key=key))


@post('/api/resetpassword', opt={CSRF_EXEMPT: True})
async def reset_password(
    data: URLEncodedBody[ResetPasswordForm],
    settings: SettingsDep,
    accounts: AccountsDep,
    passwords: PasswordsDep,
    viewer: ViewerDep,
) -> Rendered:
    try:
        account = await accounts_service.reset_password(
            settings, accounts, passwords, token=data.key, password=data.passwd, verify=data.passwd2
        )
    except InaError as error:
        if any(e.code is ErrorCode.EXPIRED for e in error.errors):
            result = FormResult(errors=error.errors, redirect='/password?expired=true')
        else:
            page = ResetPasswordPage(viewer=to_viewer(viewer), key=data.key, errors=error.errors)
            result = FormResult(errors=error.errors, page=page)
        return Rendered(content=result)
    # The new password signed out every session, this browser's included if it was theirs.
    cookies = []
    if viewer is not None and viewer.account.id == account.id:
        cookies.append(expired_session_cookie(settings))
    return Rendered(content=FormResult(redirect='/login'), cookies=cookies)
