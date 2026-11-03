"""User pages. ``/user/{name}`` is canonical; ``/u/{name}`` redirects to it, as on r2."""

from __future__ import annotations

from litestar import get
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery
from litestar.response import Redirect
from litestar.status_codes import HTTP_301_MOVED_PERMANENTLY

from app.controllers.auth import to_viewer
from app.controllers.deps import (
    AccountsDep,
    CommentsDep,
    CommunitiesDep,
    DetailsDep,
    LinksDep,
    MarksDep,
    UserDep,
    ViewerDep,
    VotesDep,
)
from app.controllers.details import Details
from app.ids import Kind
from app.repos.base import Account, AccountRepo, Comment, Link, LinkRepo, Prefs, Sort, VoteRepo
from app.services import profiles
from app.services.listings import MAX_LIMIT, TIME_FILTERS
from app.services.relations import FRIEND
from app.services.sessions import Authenticated
from app.views.accounts import Identity, Me, ProfileAbout, ProfileComment, ProfileItem, ProfilePage


async def visible_account(accounts: AccountRepo, name: str) -> Account:
    account = await accounts.by_name(name)
    if account is None or account.deleted:
        raise NotFoundException
    return account


@get(['/user/{name:str}', '/user/{name:str}/{tab:str}'])
async def show_user(
    name: FromPath[str],
    accounts: AccountsDep,
    links: LinksDep,
    comments: CommentsDep,
    votes: VotesDep,
    marks: MarksDep,
    communities: CommunitiesDep,
    details: DetailsDep,
    viewer: ViewerDep,
    tab: FromPath[str] = 'overview',
    sort: FromQuery[str] = 'new',
    t: FromQuery[str] = 'all',
    after: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
) -> ProfilePage:
    """A profile tab: overview, comments, submitted, or the account's upvoted and downvoted."""
    account = await visible_account(accounts, name)
    own = viewer is not None and viewer.account.id == account.id
    shows_votes = own or account.prefs.public_votes
    if (
        tab not in profiles.TABS
        or (tab in profiles.VOTE_TABS and not shows_votes)
        or (tab in profiles.MARK_TABS and not own)
    ):
        raise NotFoundException
    chosen = Sort(sort) if sort in {s.value for s in profiles.SORTS} else Sort.NEW
    prefs = viewer.account.prefs if viewer else Prefs()
    excluded = await communities.excluded_from_search(
        viewer.account.id if viewer else None, include_nsfw=True
    )
    request = profiles.Request(
        tab=tab,
        sort=chosen,
        time_filter=t if t in TIME_FILTERS else profiles.DEFAULT_TIME_FILTER,
        limit=max(1, min(limit or prefs.numsites, MAX_LIMIT)),
        after=await profiles.thing_by_fullname(links, comments, after) if after else None,
        own=own,
        excluded=tuple(excluded),
    )
    page = await profiles.page(links, comments, votes, marks, account.id, request)
    items = await profile_items(
        page.things, links=links, votes=votes, details=details, viewer=viewer
    )
    last = page.things[-1] if page.things else None
    friends = (
        await accounts.related_among(viewer.account.id, FRIEND, [account.id]) if viewer else set()
    )
    return ProfilePage(
        viewer=to_viewer(viewer),
        account=account,
        tab=tab,
        sort=chosen.value,
        time_filter=request.time_filter,
        items=items,
        after=str(last.fullname) if page.more and last else None,
        count=max(count, 0),
        shows_votes=shows_votes,
        is_friend=account.id in friends,
    )


async def profile_items(
    things: list[profiles.Thing],
    *,
    links: LinkRepo,
    votes: VoteRepo,
    details: Details,
    viewer: Authenticated | None,
) -> tuple[ProfileItem, ...]:
    """Rows for a profile: links as in any listing, comments with the link they are on."""
    shown_links = [thing for thing in things if isinstance(thing, Link)]
    shown_comments = [thing for thing in things if isinstance(thing, Comment)]
    threads = await links.by_ids(sorted({c.link_id for c in shown_comments}))
    extras = await details.of(shown_links, viewer, shown_comments)
    link_votes: dict[int, int] = {}
    comment_votes: dict[int, int] = {}
    if viewer is not None:
        link_votes = await votes.directions(
            viewer.account.id, Kind.LINK, [link.id for link in shown_links]
        )
        comment_votes = await votes.directions(
            viewer.account.id, Kind.COMMENT, [c.id for c in shown_comments]
        )
    items: list[ProfileItem] = []
    for thing in things:
        if isinstance(thing, Link):
            items.append(extras.row(thing, None, link_votes))
        elif thing.link_id in threads:
            items.append(
                ProfileComment(
                    thing,
                    threads[thing.link_id],
                    {1: True, -1: False}.get(comment_votes.get(thing.id, 0)),
                    extras.display.author(thing.community_id, thing.author_id),
                    saved=thing.fullname in extras.saved,
                )
            )
    return tuple(items)


@get('/user/{name:str}/about')
async def show_user_about(
    name: FromPath[str], accounts: AccountsDep, viewer: ViewerDep
) -> ProfileAbout:
    account = await visible_account(accounts, name)
    return ProfileAbout(viewer=to_viewer(viewer), account=account)


@get('/user/me')
async def show_own_user(user: UserDep) -> Redirect:
    return Redirect(f'/user/{user.account.name}')


@get('/u/{name:str}')
async def redirect_short_user(name: FromPath[str]) -> Redirect:
    return Redirect(f'/user/{name}', status_code=HTTP_301_MOVED_PERMANENTLY)


@get('/u/{name:str}/{rest:path}')
async def redirect_short_user_path(name: FromPath[str], rest: FromPath[str]) -> Redirect:
    return Redirect(f'/user/{name}{rest}', status_code=HTTP_301_MOVED_PERMANENTLY)


@get('/api/v1/me')
async def show_identity(user: UserDep) -> Identity:
    return Identity(account=user.account, modhash=user.modhash)


@get('/api/me')
async def show_me(user: UserDep) -> Me:
    return Me(account=user.account, modhash=user.modhash)
