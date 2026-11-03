"""r2's interstitial pages: "are you over eighteen?" and opting in to a quarantined community.

Saying yes to the first sets the over_18 preference, or a cookie for someone logged out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import msgspec
from litestar import Request, get, post
from litestar.datastructures import Cookie
from litestar.params import FromQuery, URLEncodedBody
from litestar.response import Redirect
from litestar.status_codes import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.controllers.auth import local_path, to_viewer
from app.controllers.consent import COMMUNITY_PATH, OVER18_COOKIE, exposed
from app.controllers.deps import AccountsDep, CommunitiesDep, SubscriptionsDep, UserDep, ViewerDep
from app.controllers.render import Rendered
from app.errors import ErrorCode, InaError
from app.views.forms import FormResult
from app.views.gates import InterstitialPage

if TYPE_CHECKING:
    from app.repos.base import Community, CommunityRepo

# A year, so the answer is remembered as r2's cookie was.
OVER18_MAX_AGE = 365 * 24 * 60 * 60


@dataclass
class Over18Form:
    over18: str = ''
    dest: str = ''


@dataclass
class QuarantineForm:
    community_name: str = ''
    accept: str = ''
    dest: str = ''


@get('/over18')
async def show_over18(viewer: ViewerDep, dest: FromQuery[str] = '/') -> InterstitialPage:
    return InterstitialPage(viewer=to_viewer(viewer), kind='over18', dest=local_path(dest, '/'))


@post('/over18')
async def answer_over18(
    data: URLEncodedBody[Over18Form], accounts: AccountsDep, viewer: ViewerDep
) -> Redirect:
    """Yes goes on to ``dest``; no goes to the front page. Logged in, it is a preference."""
    yes = data.over18 == 'yes'
    if viewer is not None:
        prefs = msgspec.structs.replace(viewer.account.prefs, over_18=yes)
        await accounts.set_prefs(viewer.account.id, prefs)
        return Redirect(local_path(data.dest, '/') if yes else '/')
    if yes:
        cookie = Cookie(key=OVER18_COOKIE, value='1', max_age=OVER18_MAX_AGE, httponly=True)
        return Redirect(local_path(data.dest, '/'), cookies=[cookie])
    return Redirect('/', cookies=[Cookie(key=OVER18_COOKIE, value='', max_age=0)])


def community_name(dest: str) -> str | None:
    match = COMMUNITY_PATH.match(urlsplit(dest).path)
    return match['name'] if match else None


@get('/quarantine')
async def show_quarantine(
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    viewer: ViewerDep,
    dest: FromQuery[str] = '/',
) -> Rendered | Redirect:
    """The quarantine interstitial for the community ``dest`` is in, if it still applies."""
    dest = local_path(dest, '/')
    name = community_name(dest)
    community = await communities.by_name(name) if name else None
    if community is None:
        return Redirect('/')
    if await exposed(subscriptions, viewer, community):
        return Redirect(dest)
    page = InterstitialPage(
        viewer=to_viewer(viewer),
        kind='quarantine',
        dest=dest,
        community_name=community.name,
        can_opt_in=viewer is not None and viewer.account.email_verified,
    )
    return Rendered(content=page)


async def quarantined(communities: CommunityRepo, name: str) -> Community:
    community = await communities.by_name(name.strip()) if name.strip() else None
    if community is None:
        raise InaError.of(
            ErrorCode.COMMUNITY_NOEXIST, 'community_name', status_code=HTTP_404_NOT_FOUND
        )
    return community


@post(['/quarantine', '/api/quarantine_optin', '/api/quarantine_optout'])
async def answer_quarantine(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[QuarantineForm],
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
) -> Rendered:
    """Opt in (with a verified email) and go on, or opt out and go to the front page."""
    community = await quarantined(communities, data.community_name)
    opting_in = request.url.path == '/api/quarantine_optin' or (
        request.url.path == '/quarantine' and data.accept == 'yes'
    )
    if opting_in and not user.account.email_verified:
        raise InaError.of(ErrorCode.QUARANTINED, status_code=HTTP_403_FORBIDDEN)
    await subscriptions.set_opt_in(user.account.id, community.id, opted_in=opting_in)
    dest = local_path(data.dest, community.path) if opting_in else '/'
    return Rendered(content=FormResult(redirect=dest))
