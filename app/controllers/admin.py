"""Site admins: admin mode, /admin, and what admins do across the site.

Admin mode is r2's /adminon and /adminoff. Only accounts made admins by scripts/admins.py see
these; to anyone else they are not there.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from litestar import Request, get, post
from litestar.background_tasks import BackgroundTask, BackgroundTasks
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.response import Redirect
from litestar.status_codes import HTTP_303_SEE_OTHER, HTTP_403_FORBIDDEN

from app.controllers.auth import RENEW_ADMIN, admin_cookie, local_path, to_viewer
from app.controllers.deps import (
    AccountsDep,
    AdminLogDep,
    CacheDep,
    CommentsDep,
    CommunitiesDep,
    LinksDep,
    ModerationDep,
    PasswordsDep,
    RedisDep,
    SettingsDep,
    UserDep,
)
from app.controllers.moderation import PAGE_SIZE, action_id, queue_items, thing_and_link
from app.controllers.queue import publish
from app.controllers.render import EXTRA_HEADERS, Rendered
from app.errors import ErrorCode, InaError
from app.ids import Fullname, Kind
from app.jobs.search import index_community, index_link
from app.repos.base import Queue, QueueEntry
from app.services import admin as service
from app.services import admin_mode, ratelimits
from app.services.listings import community_scope
from app.services.sessions import Authenticated
from app.views.admin import TABS, AdminOnPage, AdminPage
from app.views.forms import FormResult


def require_admin(user: Authenticated) -> None:
    """Admin pages are not there for anyone who isn't an admin."""
    if not user.may_admin:
        raise NotFoundException


def require_admin_mode(user: Authenticated) -> None:
    require_admin(user)
    if not user.account.is_admin:
        raise InaError.of(ErrorCode.ADMIN_MODE_REQUIRED, status_code=HTTP_403_FORBIDDEN)


def _cursor(after: str, kind: Kind) -> int | None:
    """The id of the ``kind`` thing a page continues after; one that can't be read starts over."""
    try:
        parsed = Fullname.parse(after)
    except ValueError:
        return None
    return parsed.id if parsed.kind is kind else None


@get(['/admin', '/admin/{tab:str}'])
async def show_admin(
    accounts: AccountsDep,
    communities: CommunitiesDep,
    links: LinksDep,
    comments: CommentsDep,
    moderation: ModerationDep,
    admin_log: AdminLogDep,
    user: UserDep,
    tab: FromPath[str] = 'reports',
    after: FromQuery[str] = '',
) -> Rendered | Redirect:
    """What's reported anywhere, the newest accounts and communities, and the admin log."""
    require_admin(user)
    if tab not in TABS:
        raise NotFoundException
    if not user.account.is_admin:
        return Redirect(f'/admin/on?dest=/admin/{tab}')
    page = AdminPage(viewer=to_viewer(user), tab=tab)
    if tab == 'reports':
        anchor = None
        if after:
            try:
                thing, _ = await thing_and_link(links, comments, after)
                anchor = QueueEntry(thing=thing.fullname, created=thing.created)
            except InaError:
                anchor = None
        entries = await moderation.queue(None, Queue.REPORTS, limit=PAGE_SIZE + 1, after=anchor)
        shown = entries[:PAGE_SIZE]
        page = replace(
            page,
            items=await queue_items(shown, links, comments, moderation),
            after=str(shown[-1].thing) if len(entries) > PAGE_SIZE else None,
        )
    elif tab == 'accounts':
        found = await accounts.newest(limit=PAGE_SIZE + 1, before_id=_cursor(after, Kind.ACCOUNT))
        shown_accounts = found[:PAGE_SIZE]
        page = replace(
            page,
            accounts=tuple(shown_accounts),
            after=str(shown_accounts[-1].fullname) if len(found) > PAGE_SIZE else None,
        )
    elif tab == 'communities':
        start_id = _cursor(after, Kind.COMMUNITY)
        start = await communities.by_id(start_id) if start_id is not None else None
        rows = await communities.newest(limit=PAGE_SIZE + 1, after=start, include_banned=True)
        shown_communities = rows[:PAGE_SIZE]
        page = replace(
            page,
            communities=tuple(shown_communities),
            after=str(shown_communities[-1].fullname) if len(rows) > PAGE_SIZE else None,
        )
    else:
        actions = await admin_log.actions(
            limit=PAGE_SIZE + 1, before_id=action_id(f'ModAction_{after}')
        )
        shown_actions = actions[:PAGE_SIZE]
        page = replace(
            page,
            actions=tuple(shown_actions),
            after=str(shown_actions[-1].id) if len(actions) > PAGE_SIZE else None,
        )
    return Rendered(content=page)


@get('/admin/on')
async def show_admin_on(user: UserDep, dest: FromQuery[str] = '/') -> AdminOnPage:
    require_admin(user)
    return AdminOnPage(viewer=to_viewer(user), dest=local_path(dest))


@dataclass
class AdminOnForm:
    password: str = ''
    dest: str = ''


@post('/admin/on')
async def admin_on(
    data: URLEncodedBody[AdminOnForm],
    settings: SettingsDep,
    redis: RedisDep,
    passwords: PasswordsDep,
    user: UserDep,
) -> Rendered | Redirect:
    require_admin(user)
    dest = local_path(data.dest)
    try:
        await ratelimits.enforce(redis, ratelimits.LOGIN_BY_NAME, user.account.name)
        if not await passwords.verify(user.account.password_hash, data.password):
            raise InaError.of(ErrorCode.WRONG_PASSWORD, 'password')
    except InaError as error:
        page = AdminOnPage(viewer=to_viewer(user), dest=dest, errors=error.errors)
        return Rendered(content=page)
    if user.session is None:
        raise NotFoundException
    cookie = admin_cookie(settings, admin_mode.encode(settings, user.session))
    return Redirect(dest, status_code=HTTP_303_SEE_OTHER, headers={'set-cookie': cookie})


@dataclass
class AdminOffForm:
    dest: str = ''


@post('/admin/off')
async def admin_off(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[AdminOffForm],
    settings: SettingsDep,
    user: UserDep,
) -> Redirect:
    require_admin(user)
    # Not renewed on the way out.
    request.scope['state'].get(EXTRA_HEADERS, {}).pop(RENEW_ADMIN, None)
    cookie = admin_cookie(settings, '', expire=True)
    return Redirect(
        local_path(data.dest), status_code=HTTP_303_SEE_OTHER, headers={'set-cookie': cookie}
    )


@dataclass
class SuspendForm:
    name: str = ''
    # Empty: for good.
    days: str = ''
    reason: str = ''
    dest: str = ''


@post('/api/admin/suspend')
async def suspend(
    data: URLEncodedBody[SuspendForm], accounts: AccountsDep, admin_log: AdminLogDep, user: UserDep
) -> Rendered:
    require_admin_mode(user)
    target = await service.suspend(
        accounts, admin_log, user.account, data.name, data.days, data.reason
    )
    return Rendered(content=FormResult(redirect=local_path(data.dest, f'/user/{target.name}')))


@post('/api/admin/unsuspend')
async def unsuspend(
    data: URLEncodedBody[SuspendForm], accounts: AccountsDep, admin_log: AdminLogDep, user: UserDep
) -> Rendered:
    require_admin_mode(user)
    target = await service.unsuspend(accounts, admin_log, user.account, data.name, data.reason)
    return Rendered(content=FormResult(redirect=local_path(data.dest, f'/user/{target.name}')))


@dataclass
class RemoveAllForm:
    name: str = ''
    reason: str = ''
    dest: str = ''


@post('/api/admin/remove_all')
async def remove_all(
    data: URLEncodedBody[RemoveAllForm],
    accounts: AccountsDep,
    moderation: ModerationDep,
    admin_log: AdminLogDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    """Remove everything an account posted."""
    require_admin_mode(user)
    target, removed = await service.remove_all(
        accounts, moderation, admin_log, user.account, data.name, data.reason
    )
    names = {str(r.thing) for r in removed} | {str(Fullname(Kind.LINK, r.link_id)) for r in removed}
    # Removed links leave search; the comments' threads and every listing they were in refresh.
    background = [
        BackgroundTask(cache.forget_things, sorted(names)),
        *(
            BackgroundTask(cache.bump, community_scope(community_id))
            for community_id in sorted({r.community_id for r in removed})
        ),
        *(publish(index_link, str(r.thing)) for r in removed if r.thing.kind is Kind.LINK),
    ]
    return Rendered(
        content=FormResult(redirect=local_path(data.dest, f'/user/{target.name}')),
        background=BackgroundTasks(background),
    )


@dataclass
class CommunityActionForm:
    name: str = ''
    # One of app.services.admin.COMMUNITY_ACTIONS.
    action: str = ''
    reason: str = ''
    dest: str = ''


@post('/api/admin/community')
async def act_on_community(
    data: URLEncodedBody[CommunityActionForm],
    communities: CommunitiesDep,
    admin_log: AdminLogDep,
    user: UserDep,
) -> Rendered:
    require_admin_mode(user)
    community = await service.act_on_community(
        communities, admin_log, user.account, data.name, data.action, data.reason
    )
    # Search drops a banned community, and takes it back when unbanned.
    return Rendered(
        content=FormResult(redirect=local_path(data.dest, community.path)),
        background=publish(index_community, str(community.fullname)),
    )
