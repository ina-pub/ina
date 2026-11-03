"""Moderation: the community's lists, the modlog, the queues, and acting on links and comments."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from litestar import Request, Response, get, post
from litestar.background_tasks import BackgroundTask, BackgroundTasks
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.response import Redirect
from litestar.status_codes import HTTP_403_FORBIDDEN

from app.controllers.auth import local_path, to_viewer
from app.controllers.communities import sidebar, standing_of, visible
from app.controllers.deps import (
    AccountsDep,
    AdminLogDep,
    CacheDep,
    CommentsDep,
    CommunitiesDep,
    LinksDep,
    MessagesDep,
    ModerationDep,
    RedisDep,
    SubscriptionsDep,
    UserDep,
    ViewerDep,
)
from app.controllers.queue import publish
from app.controllers.relations import change_relation
from app.controllers.render import Rendered
from app.errors import ErrorCode, FieldError, InaError
from app.ids import Fullname, Kind
from app.infra.cache import Cache
from app.jobs.inbox import deliver_message
from app.jobs.search import index_link
from app.repos.base import (
    Comment,
    CommentRepo,
    Community,
    CommunityRepo,
    Link,
    LinkRepo,
    ModerationRepo,
    Queue,
    QueueEntry,
    SubscriptionRepo,
)
from app.services import moderation as service
from app.services import ratelimits
from app.services.listings import community_scope
from app.services.moderation import RelationForm
from app.services.perms import Action, Standing, can
from app.services.relations import ACCOUNT_RELATIONS
from app.services.sessions import Authenticated
from app.views.forms import FormResult
from app.views.links import permalink
from app.views.moderation import LISTS, MembersPage, ModlogPage, QueueItem, QueuePage

PAGE_SIZE = 25
# Each relation type, and the list page that shows it.
RELATION_LISTS = {
    'banned': 'banned',
    'muted': 'muted',
    'contributor': 'contributors',
    'wikibanned': 'wikibanned',
    'wikicontributor': 'wikicontributors',
}


@dataclass
class ThingForm:
    id: str = ''
    dest: str = ''
    # /api/remove: r2 defaulted to marking as spam.
    spam: str = 'true'
    # /api/distinguish
    how: str = ''
    sticky: str = ''
    # /api/set_community_sticky
    state: str = ''
    num: str = ''


@dataclass
class ReportForm:
    thing_id: str = ''
    reason: str = ''
    other_reason: str = ''
    dest: str = ''


@dataclass
class CommunityRelationForm(RelationForm):
    # The community: by name, or by fullname as r2's container.
    community: str = ''
    container: str = ''
    dest: str = ''


@dataclass
class CommunityForm:
    community: str = ''
    id: str = ''
    dest: str = ''


def truthy(value: str) -> bool:
    return value.strip().lower() in {'true', 'on', '1', 'yes'}


async def thing_and_link(
    links: LinkRepo, comments: CommentRepo, fullname: str
) -> tuple[Link | Comment, Link]:
    try:
        thing = Fullname.parse(fullname)
    except ValueError:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id') from None
    if thing.kind is Kind.LINK and (link := await links.by_id(thing.id)) is not None:
        return link, link
    if thing.kind is Kind.COMMENT and (comment := await comments.by_id(thing.id)) is not None:
        thread = await links.by_id(comment.link_id)
        if thread is not None:
            return comment, thread
    raise InaError.of(ErrorCode.NO_THING_ID, 'id')


def refreshed(cache: Cache, link: Link, *things: Fullname) -> BackgroundTasks:
    """After the commit: drop the cached copies and the community's cached listings.

    The link is reindexed too: a removed link leaves search, an approved one returns.
    """
    names = {str(link.fullname), *(str(thing) for thing in things)}
    return BackgroundTasks(
        [
            BackgroundTask(cache.forget_things, sorted(names)),
            BackgroundTask(cache.bump, community_scope(link.community_id)),
            publish(index_link, str(link.fullname)),
        ]
    )


async def community_for(
    communities: CommunityRepo, name: str = '', fullname: str = ''
) -> Community:
    community = None
    if fullname:
        try:
            parsed = Fullname.parse(fullname)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.kind is Kind.COMMUNITY:
            community = await communities.by_id(parsed.id)
    elif name:
        community = await communities.by_name(name.strip())
    if community is None:
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'community', status_code=404)
    return community


async def members_page(
    which: str,
    community: Community,
    standing: Standing,
    *,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    moderation: ModerationRepo,
    viewer: Authenticated | None,
    errors: tuple[FieldError, ...] = (),
) -> MembersPage:
    account = viewer.account if viewer else None
    side = await sidebar(communities, subscriptions, viewer, community, standing)
    if which == 'moderators':
        invites = await moderation.invites(community.id)
        return MembersPage(
            viewer=to_viewer(viewer),
            community=community,
            sidebar=side,
            which=which,
            moderators=tuple(await communities.moderators(community.id)),
            invites=tuple(invites) if standing.full_moderator else (),
            invited=account is not None and any(i.account_id == account.id for i in invites),
            can_edit=can(account, Action.MANAGE_MODERATORS, community, standing=standing),
            errors=errors,
        )
    wiki = which in {'wikibanned', 'wikicontributors'}
    needed = Action.MANAGE_WIKI if wiki else Action.MANAGE_ACCESS
    if which == 'muted':
        needed = Action.MANAGE_MAIL
    if not can(account, needed, community, standing=standing):
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=HTTP_403_FORBIDDEN)
    lists = {
        'banned': moderation.bans,
        'muted': moderation.mutes,
        'contributors': moderation.contributors,
        'wikibanned': moderation.wiki_bans,
        'wikicontributors': moderation.wiki_contributors,
    }
    members = await lists[which](community.id)
    return MembersPage(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=side,
        which=which,
        members=tuple(members),
        can_edit=True,
        errors=errors,
    )


async def queue_items(
    entries: list[QueueEntry], links: LinkRepo, comments: CommentRepo, moderation: ModerationRepo
) -> tuple[QueueItem, ...]:
    found_links = await links.by_ids([e.thing.id for e in entries if e.thing.kind is Kind.LINK])
    found_comments = await comments.by_ids(
        [e.thing.id for e in entries if e.thing.kind is Kind.COMMENT]
    )
    threads = await links.by_ids(sorted({c.link_id for c in found_comments.values()}))
    found_links.update(threads)
    reasons = await moderation.report_reasons([e.thing for e in entries])
    items = []
    for entry in entries:
        comment = found_comments.get(entry.thing.id) if entry.thing.kind is Kind.COMMENT else None
        link_id = comment.link_id if comment else entry.thing.id
        if (link := found_links.get(link_id)) is not None:
            items.append(QueueItem(link, comment, tuple(reasons.get(entry.thing, []))))
    return tuple(items)


def action_id(after: str) -> UUID | None:
    """The mod action a page of the log continues after; one that can't be read starts over."""
    try:
        return UUID(after.removeprefix('ModAction_')) if after.startswith('ModAction_') else None
    except ValueError:
        return None


@get('/in/{name:str}/about/{where:str}')
async def show_about(
    name: FromPath[str],
    where: FromPath[str],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    viewer: ViewerDep,
    after: FromQuery[str] = '',
    mod: FromQuery[str] = '',
    type: FromQuery[str] = '',  # noqa: A002 - r2's parameter name
) -> Response[Any]:
    community, standing = await visible(communities, viewer, name)
    account = viewer.account if viewer else None
    if where in LISTS:
        page = await members_page(
            where,
            community,
            standing,
            communities=communities,
            subscriptions=subscriptions,
            moderation=moderation,
            viewer=viewer,
        )
        return Rendered(content=page)
    side = await sidebar(communities, subscriptions, viewer, community, standing)
    if where == 'log':
        if standing.mod is None:
            raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=HTTP_403_FORBIDDEN)
        actions = await moderation.actions(
            [community.id],
            limit=PAGE_SIZE + 1,
            before_id=action_id(after),
            mod_name=mod or None,
            action=type or None,
        )
        more = len(actions) > PAGE_SIZE
        actions = actions[:PAGE_SIZE]
        return Rendered(
            content=ModlogPage(
                viewer=to_viewer(viewer),
                community=community,
                sidebar=side,
                actions=tuple(actions),
                after=f'ModAction_{actions[-1].id}' if more and actions else None,
            )
        )
    if where not in Queue:
        raise NotFoundException
    if not can(account, Action.MODERATE, community, standing=standing):
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=HTTP_403_FORBIDDEN)
    anchor = None
    if after:
        try:
            thing, _ = await thing_and_link(links, comments, after)
            anchor = QueueEntry(thing=thing.fullname, created=thing.created)
        except InaError:
            anchor = None
    entries = await moderation.queue(
        [community.id], Queue(where), limit=PAGE_SIZE + 1, after=anchor
    )
    more = len(entries) > PAGE_SIZE
    entries = entries[:PAGE_SIZE]
    return Rendered(
        content=QueuePage(
            viewer=to_viewer(viewer),
            community=community,
            sidebar=side,
            queue=Queue(where),
            items=await queue_items(entries, links, comments, moderation),
            after=str(entries[-1].thing) if more and entries else None,
        )
    )


@get('/in/{name:str}/about/bouncer')
async def show_bouncer(name: FromPath[str]) -> Redirect:
    """A short address for the rules' editor in the wiki, as r2 had about/automoderator."""
    return Redirect(f'/in/{name}/wiki/edit/config/bouncer')


async def acting_on(
    data: ThingForm,
    links: LinkRepo,
    comments: CommentRepo,
    communities: CommunityRepo,
    user: Authenticated,
) -> tuple[Link | Comment, Link, Community, Standing, str]:
    thing, link = await thing_and_link(links, comments, data.id)
    community, standing = await visible(communities, user, link.community_name)
    return thing, link, community, standing, local_path(data.dest, permalink(link))


@post('/api/remove')
async def remove(
    data: URLEncodedBody[ThingForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    cache: CacheDep,
    admin_log: AdminLogDep,
    user: UserDep,
) -> Rendered:
    thing, link, community, standing, dest = await acting_on(
        data, links, comments, communities, user
    )
    await service.remove(
        moderation,
        user.account,
        standing,
        community,
        thing.fullname,
        spam=truthy(data.spam),
        was_removed_by=thing.removed_by,
        admin_log=admin_log,
    )
    return Rendered(
        content=FormResult(redirect=dest), background=refreshed(cache, link, thing.fullname)
    )


@post('/api/approve')
async def approve(
    data: URLEncodedBody[ThingForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    cache: CacheDep,
    admin_log: AdminLogDep,
    user: UserDep,
) -> Rendered:
    thing, link, community, standing, dest = await acting_on(
        data, links, comments, communities, user
    )
    await service.approve(
        moderation,
        user.account,
        standing,
        community,
        thing.fullname,
        was_removed_by=thing.removed_by,
        admin_log=admin_log,
    )
    return Rendered(
        content=FormResult(redirect=dest), background=refreshed(cache, link, thing.fullname)
    )


@post(['/api/ignore_reports', '/api/unignore_reports'])
async def ignore_reports(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[ThingForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    thing, link, community, standing, dest = await acting_on(
        data, links, comments, communities, user
    )
    ignore = request.url.path.endswith('/ignore_reports')
    await service.ignore_reports(
        moderation, user.account, standing, community, thing.fullname, ignore=ignore
    )
    return Rendered(
        content=FormResult(redirect=dest), background=refreshed(cache, link, thing.fullname)
    )


@post('/api/distinguish')
async def distinguish(
    data: URLEncodedBody[ThingForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    cache: CacheDep,
    admin_log: AdminLogDep,
    user: UserDep,
) -> Rendered:
    thing, link, community, standing, dest = await acting_on(
        data, links, comments, communities, user
    )
    await service.distinguish(
        moderation,
        user.account,
        standing,
        community,
        thing,
        data.how,
        sticky=truthy(data.sticky),
        admin_log=admin_log,
    )
    return Rendered(
        content=FormResult(redirect=dest), background=refreshed(cache, link, thing.fullname)
    )


@post('/api/set_community_sticky')
async def set_sticky(
    data: URLEncodedBody[ThingForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    thing, link, community, standing, dest = await acting_on(
        data, links, comments, communities, user
    )
    if thing is not link:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id')
    num = int(data.num) if data.num.strip().isdigit() else None
    await service.sticky(
        moderation,
        communities,
        user.account,
        standing,
        community,
        link,
        state=truthy(data.state),
        num=num,
    )
    return Rendered(content=FormResult(redirect=dest), background=refreshed(cache, link))


@post(['/api/lock', '/api/unlock'])
async def lock(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[ThingForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    thing, link, community, standing, dest = await acting_on(
        data, links, comments, communities, user
    )
    if thing is not link:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id')
    locked = request.url.path.endswith('/lock')
    await service.lock(moderation, user.account, standing, community, link, locked=locked)
    return Rendered(content=FormResult(redirect=dest), background=refreshed(cache, link))


@post(['/api/marknsfw', '/api/unmarknsfw'])
async def mark_nsfw(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[ThingForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    thing, link, community, standing, dest = await acting_on(
        data, links, comments, communities, user
    )
    if thing is not link:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id')
    nsfw = request.url.path.endswith('/marknsfw')
    await service.mark_nsfw(moderation, user.account, standing, community, link, nsfw=nsfw)
    return Rendered(content=FormResult(redirect=dest), background=refreshed(cache, link))


@post('/api/report')
async def report(
    data: URLEncodedBody[ReportForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    moderation: ModerationDep,
    redis: RedisDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    thing, link = await thing_and_link(links, comments, data.thing_id)
    community, standing = await visible(communities, user, link.community_name)
    reason = data.other_reason if data.reason == 'other' else data.reason
    dest = local_path(data.dest, permalink(link))
    try:
        await ratelimits.enforce(redis, ratelimits.REPORT_BY_ACCOUNT, str(user.account.id))
        await service.report(moderation, user.account, standing, community, thing.fullname, reason)
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        return Rendered(content=FormResult(errors=error.errors, redirect=dest))
    return Rendered(
        content=FormResult(redirect=dest), background=refreshed(cache, link, thing.fullname)
    )


async def relation_change(
    data: CommunityRelationForm,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    moderation: ModerationRepo,
    user: Authenticated,
    change: Callable[[Community, Standing], Awaitable[service.Notice | None]],
) -> Rendered:
    community = await community_for(communities, data.community, data.container)
    standing = await standing_of(communities, user, community)
    which = RELATION_LISTS.get(data.type, 'moderators')
    dest = local_path(data.dest, f'{community.path}about/{which}')
    try:
        notice = await change(community, standing)
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        page = await members_page(
            which,
            community,
            standing,
            communities=communities,
            subscriptions=subscriptions,
            moderation=moderation,
            viewer=user,
            errors=error.errors,
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    result = FormResult(redirect=dest)
    if notice is not None:
        return Rendered(
            content=result, background=publish(deliver_message, str(notice.message.fullname))
        )
    return Rendered(content=result)


@post('/api/friend')
async def friend(
    data: URLEncodedBody[CommunityRelationForm],
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    accounts: AccountsDep,
    messages: MessagesDep,
    user: UserDep,
) -> Rendered:
    """Ban, mute, approve, or invite to moderate (``type``), as r2's /api/friend.

    Without a community, ``friend`` and ``enemy`` befriend and block.
    """
    if data.type in ACCOUNT_RELATIONS:
        return await change_relation(data, data.dest, accounts, user, add=True)

    async def change(community: Community, standing: Standing) -> service.Notice | None:
        return await service.add_relation(
            moderation, communities, accounts, messages, user.account, standing, community, data
        )

    return await relation_change(data, communities, subscriptions, moderation, user, change)


@post('/api/unfriend')
async def unfriend(
    data: URLEncodedBody[CommunityRelationForm],
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    accounts: AccountsDep,
    user: UserDep,
) -> Rendered:
    if data.type in ACCOUNT_RELATIONS:
        return await change_relation(data, data.dest, accounts, user, add=False)

    async def change(community: Community, standing: Standing) -> None:
        await service.remove_relation(
            moderation, communities, accounts, user.account, standing, community, data
        )

    return await relation_change(data, communities, subscriptions, moderation, user, change)


@post('/api/setpermissions')
async def set_permissions(
    data: URLEncodedBody[CommunityRelationForm],
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    accounts: AccountsDep,
    user: UserDep,
) -> Rendered:
    async def change(community: Community, standing: Standing) -> None:
        await service.set_permissions(
            moderation, communities, accounts, user.account, standing, community, data
        )

    return await relation_change(data, communities, subscriptions, moderation, user, change)


@post('/api/accept_moderator_invite')
async def accept_invite(
    data: URLEncodedBody[CommunityForm],
    communities: CommunitiesDep,
    moderation: ModerationDep,
    user: UserDep,
) -> Rendered:
    community = await community_for(communities, data.community)
    try:
        await service.accept_invite(moderation, communities, user.account, community)
    except InaError as error:
        return Rendered(content=FormResult(errors=error.errors, redirect=community.path))
    dest = local_path(data.dest, f'{community.path}about/moderators')
    return Rendered(content=FormResult(redirect=dest))


@post('/api/leavemoderator')
async def leave_moderator(
    data: URLEncodedBody[CommunityForm],
    communities: CommunitiesDep,
    moderation: ModerationDep,
    accounts: AccountsDep,
    user: UserDep,
) -> Rendered:
    community = await community_for(communities, data.community, data.id)
    standing = await standing_of(communities, user, community)
    form = RelationForm(name=user.account.name, type='moderator')
    await service.remove_relation(
        moderation, communities, accounts, user.account, standing, community, form
    )
    return Rendered(content=FormResult(redirect=local_path(data.dest, community.path)))
