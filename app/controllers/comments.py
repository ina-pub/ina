"""A link's page and its comments: reading threads, replying, voting, and loading more."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from litestar import Request, Response, get, post
from litestar.background_tasks import BackgroundTasks
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.response import Redirect
from litestar.status_codes import HTTP_301_MOVED_PERMANENTLY, HTTP_403_FORBIDDEN
from redis.asyncio import Redis

from app.controllers.auth import CSRF_EXEMPT, WHILE_SUSPENDED, client_ip, local_path, to_viewer
from app.controllers.communities import sidebar, visible
from app.controllers.deps import (
    AccountsDep,
    CacheDep,
    CommentsDep,
    CommunitiesDep,
    DetailsDep,
    LinksDep,
    MessagesDep,
    RedisDep,
    SubscriptionsDep,
    UserDep,
    ViewerDep,
    VotesDep,
)
from app.controllers.details import Details
from app.controllers.messages import conversation_page
from app.controllers.moderation import refreshed, thing_and_link
from app.controllers.queue import publish
from app.controllers.render import Rendered
from app.errors import ErrorCode, FieldError, InaError
from app.ids import Fullname, Kind, from36
from app.infra.cache import Cache
from app.jobs.bouncer import check_new
from app.jobs.inbox import deliver_comment, deliver_message
from app.jobs.scores import recount_comment, recount_link
from app.jobs.trees import rebuild_tree
from app.repos.base import (
    AccountRepo,
    Comment,
    CommentRepo,
    Community,
    CommunityRepo,
    FlairKind,
    Link,
    LinkRepo,
    MessageRepo,
    ModPermission,
    Prefs,
    SubscriptionRepo,
    VoteRepo,
)
from app.services import comments as comments_service
from app.services import edits, messaging, ratelimits, threads
from app.services import flair as flair_service
from app.services import votes as votes_service
from app.services.comments import DEFAULT_SORT, MAX_DEPTH, MAX_LIMIT, PRECOMPUTED_THRESHOLD, SORTS
from app.services.perms import Action, can
from app.services.sessions import Authenticated
from app.views.base import Viewer
from app.views.comments import CommentNode, MoreNode, Thread, ThreadContext
from app.views.forms import FormResult
from app.views.links import DuplicatesPage, LinkPage, permalink
from app.views.messages import MessageItem

if TYPE_CHECKING:
    from app.jsontypes import JSON

# How far up a permalink's ?context= may reach, as on r2.
MAX_CONTEXT = 8
# How many other discussions of a URL its duplicates page lists.
MAX_DUPLICATES = 100
# How many comments one /api/morechildren call returns.
MORE_CHILDREN_LIMIT = 20


@dataclass
class CommentForm:
    thing_id: str = ''
    # r2's older name for thing_id.
    parent: str = ''
    text: str = ''


@dataclass
class VoteForm:
    id: str = ''
    dir: str = ''
    dest: str = ''


@dataclass(frozen=True, slots=True, kw_only=True)
class ThreadRequest:
    sort: str
    limit: int | None
    depth: int | None
    context: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Reply:
    """A reply being shown again with its errors."""

    parent: str
    text: str
    errors: tuple[FieldError, ...]


@dataclass(frozen=True, slots=True)
class Edit:
    """The author's own self post or comment, open for editing, with any errors."""

    thing: str
    text: str
    errors: tuple[FieldError, ...] = ()


@dataclass
class EditForm:
    thing_id: str = ''
    text: str = ''


@dataclass
class DeleteForm:
    id: str = ''
    dest: str = ''


def comment_sort(
    requested: str, link: Link, community: Community, viewer: Authenticated | None
) -> str:
    """The sort asked for, else the thread's suggested sort, else the viewer's, as on r2."""
    if requested in SORTS:
        return requested
    prefs = viewer.account.prefs if viewer else Prefs()
    if not prefs.ignore_suggested_sort:
        for suggested in (link.suggested_sort, community.settings.suggested_comment_sort):
            if suggested in SORTS:
                return suggested
    return prefs.default_comment_sort if prefs.default_comment_sort in SORTS else DEFAULT_SORT


async def link_by_id36(links: LinkRepo, id36: str) -> Link:
    try:
        link = await links.by_id(from36(id36))
    except ValueError:
        link = None
    if link is None:
        raise NotFoundException
    return link


async def build_page(
    link: Link,
    request: ThreadRequest,
    *,
    focus: Comment | None,
    comments: CommentRepo,
    votes: VoteRepo,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    details: Details,
    cache: Cache,
    viewer: Authenticated | None,
    reply: Reply | None = None,
    edit: Edit | None = None,
) -> LinkPage:
    community, standing = await visible(communities, viewer, link.community_name)
    account = viewer.account if viewer else None
    prefs = account.prefs if account else Prefs()
    sort = comment_sort(request.sort, link, community, viewer)
    limit = max(1, min(request.limit or prefs.num_comments, MAX_LIMIT))
    depth = max(1, min(request.depth or MAX_DEPTH, MAX_DEPTH))
    tree, known = await threads.ordered(comments, cache, link, sort)
    chain: list[Comment] = []
    if focus is not None:
        chain = await ancestors(comments, focus, request.context)
        selection = comments_service.select(tree, starts=[focus.id], limit=limit, max_depth=depth)
    else:
        selection = comments_service.select(
            tree, limit=limit, max_depth=depth, first=link.sticky_comment_id
        )
    shown = await threads.load(comments, cache, selection.ids(), known)
    for comment in chain:
        shown.setdefault(comment.id, comment)
    likes = await votes.directions(account.id, Kind.COMMENT, list(shown)) if account else {}
    extras = await details.of([link], viewer, shown.values(), known=[community])
    context = ThreadContext(
        link=link,
        community=community,
        likes=likes,
        viewer_id=account.id if account else None,
        is_moderator=standing.mod is not None,
        now=datetime.now(UTC),
        link_permalink=permalink(link),
        flair=extras.display,
        saved=extras.saved,
        friends=extras.friends,
    )
    thread = context.thread(
        selection,
        shown,
        sort=sort,
        focus=focus.fullname.id36 if focus else None,
        depth_offset=len(chain),
    )
    thread = with_ancestors(thread, chain, context, shown)
    link_likes = (await votes.directions(account.id, Kind.LINK, [link.id])) if account else {}
    archived = comments_service.is_archived(link, community)
    can_flair = account is not None and flair_service.can_flair_link(
        account, link, community, standing
    )
    choices = await details.flair.templates(community.id, FlairKind.LINK) if can_flair else []
    return LinkPage(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=await sidebar(communities, subscriptions, viewer, community, standing),
        row=extras.row(link, None, link_likes),
        thread=thread,
        can_comment=(
            can(account, Action.COMMENT, community, standing=standing)
            and not archived
            and not link.deleted
            and (not link.locked or standing.has(ModPermission.POSTS))
        ),
        locked=link.locked,
        archived=archived,
        reply_parent=reply.parent if reply else None,
        reply_text=reply.text if reply else '',
        reply_errors=reply.errors if reply else (),
        edit_thing=edit.thing if edit else None,
        edit_text=edit.text if edit else '',
        edit_errors=edit.errors if edit else (),
        can_flair=can_flair,
        link_flair_choices=tuple(choices),
    )


async def ancestors(comments: CommentRepo, focus: Comment, context: int) -> list[Comment]:
    """Up to ``context`` comments above ``focus``, outermost first."""
    chain: list[Comment] = []
    parent_id = focus.parent_id
    while parent_id is not None and len(chain) < min(max(context, 0), MAX_CONTEXT):
        parent = await comments.by_id(parent_id)
        if parent is None:
            break
        chain.append(parent)
        parent_id = parent.parent_id
    return list(reversed(chain))


def with_ancestors(
    thread: Thread, chain: list[Comment], context: ThreadContext, shown: dict[int, Comment]
) -> Thread:
    """Wrap a permalink's thread in its parents, each showing only the way down."""
    nodes = thread.nodes
    for depth, comment in reversed(list(enumerate(chain))):
        parent = replace(context.node(comment, None, shown), depth=depth, children=nodes)
        nodes = (parent,)
    return replace(thread, nodes=nodes)


def thread_request(sort: str, limit: int | None, depth: int | None, context: int) -> ThreadRequest:
    return ThreadRequest(sort=sort, limit=limit, depth=depth, context=context)


@get(['/in/{name:str}/comments/{id36:str}', '/in/{name:str}/comments/{id36:str}/{slug:str}'])
async def show_link(
    name: FromPath[str],
    id36: FromPath[str],
    links: LinksDep,
    comments: CommentsDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    details: DetailsDep,
    subscriptions: SubscriptionsDep,
    cache: CacheDep,
    viewer: ViewerDep,
    sort: FromQuery[str] = '',
    limit: FromQuery[int | None] = None,
    depth: FromQuery[int | None] = None,
    edit: FromQuery[str] = '',
) -> Response[Any]:
    link = await link_by_id36(links, id36)
    if link.community_name.lower() != name.lower():
        return Redirect(permalink(link), status_code=HTTP_301_MOVED_PERMANENTLY)
    page = await build_page(
        link,
        thread_request(sort, limit, depth, 0),
        focus=None,
        comments=comments,
        votes=votes,
        communities=communities,
        subscriptions=subscriptions,
        details=details,
        cache=cache,
        viewer=viewer,
        edit=opened(link, viewer) if edit and link.is_self else None,
    )
    return Rendered(content=page)


def opened(thing: Link | Comment, viewer: Authenticated | None) -> Edit | None:
    """``?edit``: the author's own thing, open for editing without JS."""
    if viewer is None or viewer.account.id != thing.author_id or thing.deleted:
        return None
    text = thing.selftext_md if isinstance(thing, Link) else thing.body_md
    return Edit(str(thing.fullname), text)


@get(['/in/{name:str}/duplicates/{id36:str}', '/in/{name:str}/duplicates/{id36:str}/{slug:str}'])
async def show_duplicates(
    id36: FromPath[str],
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    details: DetailsDep,
    viewer: ViewerDep,
) -> DuplicatesPage:
    """Other links to the same URL, in communities the viewer may see: r2's other discussions."""
    link = await link_by_id36(links, id36)
    community, standing = await visible(communities, viewer, link.community_name)
    account = viewer.account if viewer else None
    others: list[Link] = []
    if not link.is_self:
        found = await links.same_url(link.url, exclude=link.id, limit=MAX_DUPLICATES)
        closed = set(
            await communities.excluded_from_search(
                account.id if account else None,
                include_nsfw=account is not None and account.prefs.over_18,
            )
        )
        others = [other for other in found if other.community_id not in closed]
    shown = [link, *others]
    directions = (
        await votes.directions(account.id, Kind.LINK, [each.id for each in shown])
        if account
        else {}
    )
    extras = await details.of(shown, viewer, known=[community])
    return DuplicatesPage(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=await sidebar(communities, subscriptions, viewer, community, standing),
        row=extras.row(link, None, directions),
        others=tuple(extras.row(other, None, directions) for other in others),
    )


@get('/in/{name:str}/comments/{id36:str}/{slug:str}/{comment36:str}')
async def show_comment(
    name: FromPath[str],
    id36: FromPath[str],
    comment36: FromPath[str],
    links: LinksDep,
    comments: CommentsDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    details: DetailsDep,
    subscriptions: SubscriptionsDep,
    cache: CacheDep,
    viewer: ViewerDep,
    sort: FromQuery[str] = '',
    limit: FromQuery[int | None] = None,
    depth: FromQuery[int | None] = None,
    context: FromQuery[int] = 0,
    edit: FromQuery[str] = '',
) -> Response[Any]:
    link = await link_by_id36(links, id36)
    try:
        focus = await comments.by_id(from36(comment36))
    except ValueError:
        focus = None
    if focus is None or focus.link_id != link.id:
        raise NotFoundException
    if link.community_name.lower() != name.lower():
        return Redirect(f'{permalink(link)}{comment36}/', status_code=HTTP_301_MOVED_PERMANENTLY)
    page = await build_page(
        link,
        thread_request(sort, limit, depth, context),
        focus=focus,
        comments=comments,
        votes=votes,
        communities=communities,
        subscriptions=subscriptions,
        details=details,
        cache=cache,
        viewer=viewer,
        edit=opened(focus, viewer) if edit else None,
    )
    return Rendered(content=page)


async def parent_of(
    links: LinkRepo, comments: CommentRepo, fullname: str
) -> tuple[Link, Comment | None]:
    """The thread and the comment being replied to (None when replying to the link)."""
    try:
        parsed = Fullname.parse(fullname)
    except ValueError:
        raise InaError.of(ErrorCode.NO_THING_ID, 'parent', status_code=HTTP_403_FORBIDDEN) from None
    link: Link | None = None
    parent: Comment | None = None
    if parsed.kind is Kind.LINK:
        link = await links.by_id(parsed.id)
    elif parsed.kind is Kind.COMMENT:
        parent = await comments.by_id(parsed.id)
        link = await links.by_id(parent.link_id) if parent else None
    if link is None:
        raise InaError.of(ErrorCode.NO_THING_ID, 'parent', status_code=HTTP_403_FORBIDDEN)
    return link, parent


def render_fragment(request: Request[Any, Any, Any], template: str, **context: Any) -> str:  # noqa: ANN401
    engine = request.app.template_engine
    if engine is None:
        msg = "rendering a fragment needs the app's template_config"
        raise RuntimeError(msg)
    rendered = engine.get_template(template).render(
        request=request, now=datetime.now(UTC), **context
    )
    return str(rendered)


def render_node(request: Request[Any, Any, Any], node: CommentNode, viewer: Viewer | None) -> str:
    """One comment's HTML, for the site's JS to insert, as r2's ``content`` field was."""
    return render_fragment(request, 'fragments/comment.html', node=node, viewer=viewer)


@post('/api/comment')
async def comment(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[CommentForm],
    links: LinksDep,
    comments: CommentsDep,
    messages: MessagesDep,
    accounts: AccountsDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    details: DetailsDep,
    subscriptions: SubscriptionsDep,
    redis: RedisDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    parent_name = data.thing_id or data.parent
    if parent_name.startswith(f'{Kind.MESSAGE}_'):
        return await message_reply(
            request, parent_name, data.text, messages, communities, accounts, redis, user
        )
    link, parent = await parent_of(links, comments, parent_name)
    community, standing = await visible(communities, user, link.community_name)
    try:
        await ratelimits.enforce(redis, ratelimits.COMMENT_BY_ACCOUNT, str(user.account.id))
        created = await comments_service.post(
            comments,
            votes,
            user.account,
            link,
            community,
            parent,
            data.text,
            standing=standing,
            ip=client_ip(request),
        )
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        page = await build_page(
            link,
            thread_request('', None, None, 0),
            focus=parent,
            comments=comments,
            votes=votes,
            communities=communities,
            subscriptions=subscriptions,
            details=details,
            cache=cache,
            viewer=user,
            reply=Reply(parent=parent_name, text=data.text, errors=error.errors),
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    extras = await details.of([], user, [created], known=[community])
    context = ThreadContext(
        link=link,
        community=community,
        likes={created.id: 1},
        viewer_id=user.account.id,
        is_moderator=standing.mod is not None,
        now=datetime.now(UTC),
        link_permalink=permalink(link),
        flair=extras.display,
        saved=extras.saved,
        friends=extras.friends,
    )
    node = replace(context.node(created, None, {}), depth=created.depth)
    content = render_node(request, node, to_viewer(user))
    thing: dict[str, Any] = {
        'kind': 't1',
        'data': {
            **node.data(''),
            'content': content,
            'id': str(created.fullname),
            'parent': str(created.parent_fullname),
            'link': str(link.fullname),
        },
    }
    result = FormResult(redirect=f'{node.permalink}?context=1', data={'things': [thing]})
    tasks = [
        publish(recount_comment, str(created.fullname)),
        publish(deliver_comment, str(created.fullname)),
    ]
    if community.bouncer_rules.strip():
        tasks.append(publish(check_new, str(created.fullname)))
    if link.comment_count + 1 >= PRECOMPUTED_THRESHOLD:
        tasks.append(publish(rebuild_tree, str(link.fullname)))
    return Rendered(content=result, background=BackgroundTasks(tasks))


async def message_reply(
    request: Request[Any, Any, Any],
    parent_name: str,
    text: str,
    messages: MessageRepo,
    communities: CommunityRepo,
    accounts: AccountRepo,
    redis: Redis,
    user: Authenticated,
) -> Rendered:
    """A reply in a conversation: r2 took these through /api/comment too, with a t4 parent."""
    try:
        parent = await messages.by_id(Fullname.parse(parent_name).id)
    except ValueError:
        parent = None
    if parent is None:
        raise InaError.of(ErrorCode.NO_THING_ID, 'parent', status_code=HTTP_403_FORBIDDEN)
    try:
        await ratelimits.enforce(redis, ratelimits.MESSAGE_BY_ACCOUNT, str(user.account.id))
        sent = await messaging.reply(
            messages,
            communities,
            user.account,
            parent,
            text,
            mail_communities=list(user.mail_communities),
            ip=client_ip(request),
        )
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        page = await conversation_page(
            messages,
            accounts,
            user,
            Fullname(Kind.MESSAGE, parent.first_message_id).id36,
            reply_text=text,
            reply_errors=error.errors,
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    item = MessageItem(sent, unread=False)
    content = render_fragment(request, 'fragments/message.html', item=item, viewer=to_viewer(user))
    data = item.to_json()
    assert isinstance(data, dict)  # noqa: S101 - to_json builds a dict
    inner = data['data']
    assert isinstance(inner, dict)  # noqa: S101 - likewise
    thing: JSON = {
        'kind': 't4',
        'data': {**inner, 'content': content, 'id': str(sent.fullname), 'parent': parent_name},
    }
    result = FormResult(redirect=item.permalink, data={'things': [thing]})
    return Rendered(content=result, background=publish(deliver_message, str(sent.fullname)))


async def vote_target(
    links: LinkRepo, comments: CommentRepo, fullname: str
) -> tuple[Fullname, Link, bool]:
    """The thing voted on, its thread, and whether the thing is deleted."""
    try:
        thing = Fullname.parse(fullname)
    except ValueError:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id') from None
    if thing.kind is Kind.LINK:
        link = await links.by_id(thing.id)
        if link is not None:
            return thing, link, link.deleted
    elif thing.kind is Kind.COMMENT:
        target = await comments.by_id(thing.id)
        link = await links.by_id(target.link_id) if target else None
        if target is not None and link is not None:
            return thing, link, target.deleted
    raise InaError.of(ErrorCode.NO_THING_ID, 'id')


@post('/api/vote')
async def vote(
    data: URLEncodedBody[VoteForm],
    links: LinksDep,
    comments: CommentsDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    redis: RedisDep,
    user: UserDep,
) -> Rendered:
    """Vote ``dir`` 1, 0 or -1 on ``id``; the score job then recounts it."""
    try:
        thing, link, deleted = await vote_target(links, comments, data.id)
    except InaError as error:
        return Rendered(content=FormResult(errors=error.errors, redirect=local_path(data.dest)))
    community, standing = await visible(communities, user, link.community_name)
    dest = local_path(data.dest, permalink(link))
    try:
        direction = int(data.dir)
    except ValueError:
        direction = 2
    try:
        await ratelimits.enforce(redis, ratelimits.VOTE_BY_ACCOUNT, str(user.account.id))
        await votes_service.cast(
            votes,
            user.account,
            thing,
            direction,
            link=link,
            community=community,
            deleted=deleted,
            standing=standing,
        )
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        return Rendered(content=FormResult(errors=error.errors, redirect=dest))
    actor = recount_link if thing.kind is Kind.LINK else recount_comment
    result = FormResult(redirect=dest)
    return Rendered(content=result, background=publish(actor, str(thing)))


# Reading only, so no CSRF token is needed, as on r2.
@post('/api/editusertext')
async def edit_usertext(
    data: URLEncodedBody[EditForm],
    links: LinksDep,
    comments: CommentsDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    details: DetailsDep,
    subscriptions: SubscriptionsDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    """Edit a self post's text or a comment's body; without JS, back to its permalink."""
    thing, link = await thing_and_link(links, comments, data.thing_id)
    community, _ = await visible(communities, user, link.community_name)
    try:
        html = await edits.edit(links, comments, user.account, thing, link, community, data.text)
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        page = await build_page(
            link,
            thread_request('', None, None, 0),
            focus=thing if isinstance(thing, Comment) else None,
            comments=comments,
            votes=votes,
            communities=communities,
            subscriptions=subscriptions,
            details=details,
            cache=cache,
            viewer=user,
            edit=Edit(data.thing_id, data.text, error.errors),
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    target = permalink(link)
    if isinstance(thing, Comment):
        target = f'{target}{thing.fullname.id36}/'
    kind = 't3' if isinstance(thing, Link) else 't1'
    found: dict[str, JSON] = {
        'id': str(thing.fullname),
        'contentText': data.text,
        'contentHTML': html,
    }
    result = FormResult(redirect=target, data={'things': [{'kind': kind, 'data': found}]})
    return Rendered(content=result, background=refreshed(cache, link, thing.fullname))


@post('/api/del', opt={WHILE_SUSPENDED: True})
async def delete_thing(
    data: URLEncodedBody[DeleteForm],
    links: LinksDep,
    comments: CommentsDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    """The author deletes their link or comment; r2 answered with an empty object."""
    thing, link = await thing_and_link(links, comments, data.id)
    deleted = await edits.delete(links, comments, user.account, thing)
    tasks = list(refreshed(cache, link, thing.fullname).tasks)
    if deleted:
        recount = recount_link if isinstance(thing, Link) else recount_comment
        tasks.append(publish(recount, str(thing.fullname)))
    dest = local_path(data.dest, permalink(link))
    return Rendered(content=FormResult(redirect=dest), background=BackgroundTasks(tasks))


@post(['/api/morechildren'], opt={CSRF_EXEMPT: True})
async def more_children(
    request: Request[Any, Any, Any],
    links: LinksDep,
    comments: CommentsDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    details: DetailsDep,
    cache: CacheDep,
    viewer: ViewerDep,
) -> Rendered:
    """Comments behind a "load more comments": ``children`` id36s under ``link_id``.

    Answers r2's way: a flat list of things in thread order, each with ``parent`` and its own
    rendered ``content``, for the JS to insert under its parent.
    """
    form = await request.form()
    link_name = str(form.get('link_id', ''))
    children = [i for i in str(form.get('children', '')).split(',') if i]
    try:
        link_id = Fullname.parse(link_name)
        ids = [from36(i) for i in children]
    except ValueError:
        raise InaError.of(ErrorCode.NO_THING_ID, 'link_id') from None
    link = await links.by_id(link_id.id) if link_id.kind is Kind.LINK else None
    if link is None:
        raise InaError.of(ErrorCode.NO_THING_ID, 'link_id')
    community, standing = await visible(communities, viewer, link.community_name)
    sort = comment_sort(str(form.get('sort', '')), link, community, viewer)
    tree, known = await threads.ordered(comments, cache, link, sort)
    parent_of_child = comments_service.parents(tree)
    selection = comments_service.select(
        tree,
        starts=ids,
        parent_id=parent_of_child.get(ids[0]) if ids else None,
        limit=MORE_CHILDREN_LIMIT,
    )
    shown = await threads.load(comments, cache, selection.ids(), known)
    account = viewer.account if viewer else None
    likes = await votes.directions(account.id, Kind.COMMENT, list(shown)) if account else {}
    extras = await details.of([], viewer, shown.values(), known=[community])
    context = ThreadContext(
        link=link,
        community=community,
        likes=likes,
        viewer_id=account.id if account else None,
        is_moderator=standing.mod is not None,
        now=datetime.now(UTC),
        link_permalink=permalink(link),
        flair=extras.display,
        saved=extras.saved,
        friends=extras.friends,
    )
    base = shown[selection.roots[0].id].depth if selection.roots else 0
    thread = context.thread(selection, shown, sort=sort, depth_offset=base)
    things: list[dict[str, Any]] = []
    flatten(thread.nodes, request, to_viewer(viewer), things)
    if thread.more is not None:
        things.append(more_thing(request, thread.more))
    return Rendered(content=FormResult(data={'things': cast('JSON', things)}))


def flatten(
    nodes: tuple[CommentNode, ...],
    request: Request[Any, Any, Any],
    viewer: Viewer | None,
    things: list[dict[str, Any]],
) -> None:
    for node in nodes:
        alone = replace(node, children=(), more=None)
        things.append(
            {
                'kind': 't1',
                'data': {
                    **node.data(''),
                    'content': render_node(request, alone, viewer),
                    'id': str(node.comment.fullname),
                    'parent': str(node.comment.parent_fullname),
                },
            }
        )
        flatten(node.children, request, viewer, things)
        if node.more is not None:
            things.append(more_thing(request, node.more))


def more_thing(request: Request[Any, Any, Any], more: MoreNode) -> dict[str, Any]:
    thing = more.to_json()
    data = thing['data']
    assert isinstance(data, dict)  # noqa: S101 - built just above as a dict
    return {
        **thing,
        'data': {**data, 'content': render_fragment(request, 'fragments/more.html', more=more)},
        'parent': more.parent,
    }
