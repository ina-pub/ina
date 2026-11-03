"""``/prefs``: options, email and password."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from litestar import Request, get, post
from litestar.params import FromQuery, URLEncodedBody

from app.controllers.auth import WHILE_SUSPENDED, session_cookie, to_viewer
from app.controllers.deps import AccountsDep, PasswordsDep, SettingsDep, UserDep
from app.controllers.render import Rendered
from app.errors import InaError
from app.services import accounts as accounts_service
from app.services import prefs as prefs_service
from app.services import sessions
from app.services.sessions import Authenticated
from app.views.forms import FormResult
from app.views.prefs import PrefsJson, PrefsPage, PrefsUpdatePage


@dataclass
class PasswordChangeForm:
    curpass: str = ''
    newpass: str = ''
    verpass: str = ''


@dataclass
class EmailChangeForm:
    curpass: str = ''
    email: str = ''


def update_page(user: Authenticated, error: InaError) -> PrefsUpdatePage:
    return PrefsUpdatePage(
        viewer=to_viewer(user),
        email=user.account.email,
        email_verified=user.account.email_verified,
        errors=error.errors,
    )


@get(['/prefs', '/prefs/options'])
async def show_prefs(
    user: UserDep, done: FromQuery[str] = '', generic_error: FromQuery[str | None] = None
) -> PrefsPage:
    return PrefsPage(
        viewer=to_viewer(user), prefs=user.account.prefs, done=done == 'true', error=generic_error
    )


@post('/post/options', opt={WHILE_SUSPENDED: True})
async def update_prefs(
    request: Request[Any, Any, Any], user: UserDep, accounts: AccountsDep
) -> Rendered:
    form = await request.form()
    submitted = {name: value for name, value in form.items() if isinstance(value, str)}
    try:
        prefs = prefs_service.from_form(user.account.prefs, submitted)
    except InaError as error:
        query = urlencode({'generic_error': error.errors[0].code})
        return Rendered(content=FormResult(errors=error.errors, redirect=f'/prefs?{query}'))
    await accounts.set_prefs(user.account.id, prefs)
    return Rendered(content=FormResult(redirect='/prefs?done=true'))


@get('/prefs/update')
async def show_prefs_update(user: UserDep, done: FromQuery[str] = '') -> PrefsUpdatePage:
    return PrefsUpdatePage(
        viewer=to_viewer(user),
        email=user.account.email,
        email_verified=user.account.email_verified,
        done=done or None,
    )


@post('/api/update_password', opt={WHILE_SUSPENDED: True})
async def update_password(
    data: URLEncodedBody[PasswordChangeForm],
    settings: SettingsDep,
    user: UserDep,
    accounts: AccountsDep,
    passwords: PasswordsDep,
) -> Rendered:
    try:
        version = await accounts_service.change_password(
            accounts,
            passwords,
            user.account,
            current=data.curpass,
            password=data.newpass,
            verify=data.verpass,
        )
    except InaError as error:
        return Rendered(content=FormResult(errors=error.errors, page=update_page(user, error)))
    # Every other session is now signed out; keep this one with a fresh cookie.
    remember = user.session is not None and user.session.remember
    session = sessions.start(user.account, remember=remember, version=version)
    result = FormResult(redirect='/prefs/update?done=password', data={'modhash': session.csrf})
    return Rendered(content=result, cookies=[session_cookie(settings, session)])


@post('/api/update_email', opt={WHILE_SUSPENDED: True})
async def update_email(
    data: URLEncodedBody[EmailChangeForm],
    user: UserDep,
    accounts: AccountsDep,
    passwords: PasswordsDep,
) -> Rendered:
    try:
        await accounts_service.change_email(
            accounts, passwords, user.account, current=data.curpass, email=data.email
        )
    except InaError as error:
        return Rendered(content=FormResult(errors=error.errors, page=update_page(user, error)))
    return Rendered(content=FormResult(redirect='/prefs/update?done=email'))


@get('/api/v1/me/prefs')
async def show_prefs_json(user: UserDep) -> PrefsJson:
    return PrefsJson(prefs=user.account.prefs)
