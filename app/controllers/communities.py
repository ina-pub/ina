"""Community pages, settings, subscriptions and the ``/ins`` directory."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime

from litestar import get, post
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.status_codes import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.controllers.auth import WHILE_SUSPENDED, local_path, to_viewer
from app.controllers.deps import (
    CacheDep,
    CommunitiesDep,
    RedisDep,
    SettingsDep,
    SubscriptionsDep,
    UserDep,
    ViewerDep,
    WikiDep,
)
from app.controllers.queue import publish
from app.controllers.render import Rendered
from app.errors import ErrorCode, InaError
from app.ids import Fullname, Kind
from app.jobs.search import index_community
from app.repos.base import Community, CommunityRepo, ModPermission, SubscriptionRepo
from app.services import communities as communities_service
from app.services import listings, ratelimits, traffic
from app.services import wiki as wiki_service
from app.services.communities import CommunityForm
from app.services.perms import NOBODY, Action, Standing, can
from app.services.sessions import Authenticated
from app.views.communities import (
    CommunitiesPage,
    CommunityAbout,
    CommunityEditPage,
    CommunityRow,
    Sidebar,
)
from app.views.forms import FormResult
from app.views.traffic import TrafficPage

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


@dataclass
class SiteAdminForm(CommunityForm):
    """``/api/site_admin``: the settings form, plus ``community`` naming it when editing."""

    community: str = ''


@dataclass
class SubscribeForm:
    action: str = ''
    community: str = ''
    community_name: str = ''
    dest: str = ''


async def by_fullname(communities: CommunityRepo, fullname: str) -> Community | None:
    try:
        parsed = Fullname.parse(fullname)
    except ValueError:
        return None
    return await communities.by_id(parsed.id) if parsed.kind is Kind.COMMUNITY else None


async def find(communities: CommunityRepo, name: str) -> Community:
    community = await communities.by_name(name)
    if community is None:
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, status_code=HTTP_404_NOT_FOUND)
    return community


async def standing_of(
    communities: CommunityRepo, viewer: Authenticated | None, community: Community
) -> Standing:
    if viewer is None:
        return NOBODY
    mod, contributor, banned = await communities.standing(community.id, viewer.account.id)
    if viewer.account.is_admin:
        # In admin mode an admin keeps every community, as r2's admins could.
        return Standing(ModPermission.ALL, contributor=True, admin=mod is None)
    return Standing(mod, contributor, banned)


async def visible(
    communities: CommunityRepo, viewer: Authenticated | None, name: str
) -> tuple[Community, Standing]:
    """The community and the viewer's standing there.

    403 if it is private to them, and 404, saying so, if the site's admins banned it.
    """
    community = await find(communities, name)
    if community.banned and not (viewer and viewer.account.is_admin):
        raise InaError.of(
            ErrorCode.COMMUNITY_BANNED, status_code=HTTP_404_NOT_FOUND, name=community.name
        )
    standing = await standing_of(communities, viewer, community)
    if not can(viewer.account if viewer else None, Action.VIEW, community, standing=standing):
        raise InaError.of(ErrorCode.PRIVATE_COMMUNITY, status_code=HTTP_403_FORBIDDEN)
    return community, standing


async def sidebar(
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    viewer: Authenticated | None,
    community: Community,
    standing: Standing,
) -> Sidebar:
    account = viewer.account if viewer else None
    subscribed = (
        await subscriptions.subscribed_among(account.id, [community.id]) if account else set()
    )
    return Sidebar(
        moderators=tuple(await communities.moderators(community.id)),
        is_subscriber=community.id in subscribed,
        is_moderator=standing.mod is not None,
        can_configure=can(account, Action.CONFIGURE, community, standing=standing),
        can_moderate=can(account, Action.MODERATE, community, standing=standing),
        can_manage_access=can(account, Action.MANAGE_ACCESS, community, standing=standing),
        can_manage_wiki=can(account, Action.MANAGE_WIKI, community, standing=standing),
        can_manage_mail=can(account, Action.MANAGE_MAIL, community, standing=standing),
        can_see_traffic=standing.mod is not None or community.settings.public_traffic,
        can_manage_flair=standing.has(ModPermission.FLAIR),
        wiki_enabled=community.settings.wikimode != 'disabled' or standing.has(ModPermission.WIKI),
    )


@get('/in/{name:str}/about')
async def show_community_about(
    name: FromPath[str],
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    viewer: ViewerDep,
) -> CommunityAbout:
    community, standing = await visible(communities, viewer, name)
    return CommunityAbout(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=await sidebar(communities, subscriptions, viewer, community, standing),
    )


@get('/in/{name:str}/about/edit')
async def show_community_edit(
    name: FromPath[str], communities: CommunitiesDep, user: UserDep, done: FromQuery[str] = ''
) -> CommunityEditPage:
    community, standing = await visible(communities, user, name)
    if not can(user.account, Action.CONFIGURE, community, standing=standing):
        raise InaError.of(ErrorCode.COMMUNITY_NOTALLOWED, status_code=HTTP_403_FORBIDDEN)
    return CommunityEditPage(
        viewer=to_viewer(user),
        community=community,
        form=CommunityForm.of(community),
        done=done == 'true',
    )


@get('/ins/create')
async def show_community_create(user: UserDep) -> CommunityEditPage:
    return CommunityEditPage(viewer=to_viewer(user), community=None, form=CommunityForm())


@post('/api/site_admin')
async def site_admin(
    data: URLEncodedBody[SiteAdminForm],
    settings: SettingsDep,
    communities: CommunitiesDep,
    wiki: WikiDep,
    redis: RedisDep,
    user: UserDep,
) -> Rendered:
    """Create a community, or save an existing one's settings when ``community`` names it."""
    form = CommunityForm(
        **{field.name: getattr(data, field.name) for field in fields(CommunityForm)}
    )
    existing = await by_fullname(communities, data.community) if data.community else None
    if data.community and existing is None:
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'community', status_code=HTTP_404_NOT_FOUND)
    try:
        if existing is None:
            await ratelimits.enforce(
                redis, ratelimits.CREATE_COMMUNITY_BY_ACCOUNT, str(user.account.id)
            )
            community = await communities_service.create(communities, settings, user.account, form)
            redirect = community.path
        else:
            standing = await standing_of(communities, user, existing)
            if not can(user.account, Action.CONFIGURE, existing, standing=standing):
                raise InaError.of(
                    ErrorCode.COMMUNITY_NOTALLOWED, 'community', status_code=HTTP_403_FORBIDDEN
                )
            community = await communities_service.update(communities, existing, form)
            await wiki_service.record_settings(wiki, user.account, existing, community)
            redirect = f'{community.path}about/edit?done=true'
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        page = CommunityEditPage(
            viewer=to_viewer(user), community=existing, form=form, errors=error.errors
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    data_json = {'name': str(community.fullname), 'url': community.path}
    return Rendered(
        content=FormResult(redirect=redirect, data=data_json),
        background=publish(index_community, str(community.fullname)),
    )


@post('/api/subscribe', opt={WHILE_SUSPENDED: True})
async def subscribe(
    data: URLEncodedBody[SubscribeForm],
    settings: SettingsDep,
    cache: CacheDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
) -> Rendered:
    """``action`` is ``sub`` or ``unsub``; ``community`` (a fullname) or ``community_name``."""
    if data.action not in {'sub', 'unsub'}:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'action')
    if data.community:
        community = await by_fullname(communities, data.community)
    else:
        community = await communities.by_name(data.community_name) if data.community_name else None
    if community is None:
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'community', status_code=HTTP_404_NOT_FOUND)
    standing = await standing_of(communities, user, community)
    added = await communities_service.set_subscription(
        subscriptions,
        user.account,
        community,
        subscribe=data.action == 'sub',
        standing=standing,
        defaults=await listings.default_ids(settings, communities, cache),
    )
    if added:
        await traffic.record_subscription(cache.redis, community.id)
    # Subscriber counts rank community search results.
    return Rendered(
        content=FormResult(redirect=local_path(data.dest, community.path)),
        background=publish(index_community, str(community.fullname)),
    )


async def directory(
    where: str,
    rows: list[Community],
    subscriptions: SubscriptionRepo,
    viewer: Authenticated | None,
    limit: int,
) -> CommunitiesPage:
    subscribed = (
        await subscriptions.subscribed_among(viewer.account.id, [s.id for s in rows])
        if viewer
        else set()
    )
    after = str(rows[-1].fullname) if len(rows) == limit else None
    return CommunitiesPage(
        viewer=to_viewer(viewer),
        where=where,
        rows=tuple(CommunityRow(s, s.id in subscribed) for s in rows),
        after=after,
    )


def page_size(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


@get(['/ins', '/ins/popular'])
async def show_popular(
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    viewer: ViewerDep,
    after: FromQuery[str] = '',
    limit: FromQuery[int] = DEFAULT_LIMIT,
) -> CommunitiesPage:
    limit = page_size(limit)
    start = await by_fullname(communities, after) if after else None
    rows = await communities.popular(limit=limit, after=start)
    return await directory('popular', rows, subscriptions, viewer, limit)


@get('/ins/new')
async def show_newest(
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    viewer: ViewerDep,
    after: FromQuery[str] = '',
    limit: FromQuery[int] = DEFAULT_LIMIT,
) -> CommunitiesPage:
    limit = page_size(limit)
    start = await by_fullname(communities, after) if after else None
    rows = await communities.newest(limit=limit, after=start)
    return await directory('new', rows, subscriptions, viewer, limit)


@get(['/ins/mine', '/ins/mine/subscriber'])
async def show_mine(
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
    after: FromQuery[str] = '',
    limit: FromQuery[int] = DEFAULT_LIMIT,
) -> CommunitiesPage:
    limit = page_size(limit)
    start = await by_fullname(communities, after) if after else None
    rows = await subscriptions.subscribed(user.account.id, limit=limit, after=start)
    return await directory('mine/subscriber', rows, subscriptions, user, limit)


@get('/ins/mine/moderator')
async def show_moderated(
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
    after: FromQuery[str] = '',
    limit: FromQuery[int] = DEFAULT_LIMIT,
) -> CommunitiesPage:
    limit = page_size(limit)
    start = await by_fullname(communities, after) if after else None
    rows = await communities.moderated_by(user.account.id, limit=limit, after=start)
    return await directory('mine/moderator', rows, subscriptions, user, limit)


@get('/in/{name:str}/about/traffic')
async def show_traffic(
    name: FromPath[str],
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    redis: RedisDep,
    viewer: ViewerDep,
) -> TrafficPage:
    """For moderators, or anyone when the community makes its traffic public, as on r2."""
    community, standing = await visible(communities, viewer, name)
    if standing.mod is None and not community.settings.public_traffic:
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=HTTP_403_FORBIDDEN)
    now = datetime.now(UTC)
    return TrafficPage(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=await sidebar(communities, subscriptions, viewer, community, standing),
        hours=tuple(await traffic.history(redis, community.id, traffic.HOUR, now=now)),
        days=tuple(await traffic.history(redis, community.id, traffic.DAY, now=now)),
        months=tuple(await traffic.history(redis, community.id, traffic.MONTH, now=now)),
    )
