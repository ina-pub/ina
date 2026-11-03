"""Whether a page may show yet: consent before NSFW communities and links, and quarantine opt-ins.

A page in an NSFW community, or an NSFW link's page, first asks "are you over eighteen?" at
/over18. A quarantined community is only for accounts with a verified email that opted in at
/quarantine; its JSON answers 403 until then, as r2's did. The pages are in gates.py.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from litestar import Request
from litestar.response import Redirect
from litestar.status_codes import HTTP_403_FORBIDDEN

from app.controllers.render import wants_json
from app.errors import ErrorCode, InaError
from app.ids import from36

if TYPE_CHECKING:
    from app.repos.base import Community, CommunityRepo, LinkRepo, SubscriptionRepo
    from app.services.sessions import Authenticated

OVER18_COOKIE = 'over18'
# Where each interstitial is.
OVER18_PATH = '/over18'
QUARANTINE_PATH = '/quarantine'
# A community's pages, and with a link's id36 when it is the link's page.
COMMUNITY_PATH = re.compile(r'^/in/(?P<name>[^/]+)(?:/comments/(?P<id36>[^/]+))?')
# Paths under /in/ that are not one community.
NOT_A_COMMUNITY = frozenset({'all', 'random'})


class Interstitial(Exception):  # noqa: N818 - it is not an error, it sends the browser elsewhere
    """Send the browser to an interstitial page first, then back to ``dest``."""

    def __init__(self, path: str, dest: str) -> None:
        super().__init__(path)
        self.location = f'{path}?{urlencode({"dest": dest})}'


def handle_interstitial(_: Request[Any, Any, Any], error: Interstitial) -> Redirect:
    return Redirect(error.location)


def consented(request: Request[Any, Any, Any], viewer: Authenticated | None) -> bool:
    """Over eighteen and willing to see adult content, by preference or by cookie."""
    if viewer is not None:
        return viewer.account.prefs.over_18
    return request.cookies.get(OVER18_COOKIE) == '1'


async def exposed(
    subscriptions: SubscriptionRepo, viewer: Authenticated | None, community: Community
) -> bool:
    """r2's is_exposed: anyone, unless quarantined; then only those who opted in, and admins."""
    if not community.settings.quarantine or (viewer and viewer.account.is_admin):
        return True
    if viewer is None or not viewer.account.email_verified:
        return False
    return await subscriptions.opted_in(viewer.account.id, community.id)


async def check(
    request: Request[Any, Any, Any],
    viewer: Authenticated | None,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    links: LinkRepo,
) -> Community | None:
    """Raise Interstitial (or 403 for JSON) where a page needs consent or an opt-in first.

    Return the community a page it lets through is in, if it is in one.
    """
    match = COMMUNITY_PATH.match(request.url.path)
    if request.method != 'GET' or match is None:
        return None
    name = match['name']
    if '+' in name or name.lower() in NOT_A_COMMUNITY:
        return None
    community = await communities.by_name(name)
    if community is None:
        return None
    dest = request.url.path + (f'?{request.url.query}' if request.url.query else '')
    if not await exposed(subscriptions, viewer, community):
        if wants_json(request):
            raise InaError.of(ErrorCode.QUARANTINED, status_code=HTTP_403_FORBIDDEN)
        raise Interstitial(QUARANTINE_PATH, dest)
    if wants_json(request) or consented(request, viewer):
        return community
    nsfw = community.nsfw
    if not nsfw and match['id36']:
        try:
            link = await links.by_id(from36(match['id36']))
        except ValueError:
            link = None
        nsfw = link is not None and link.nsfw
    if nsfw:
        raise Interstitial(OVER18_PATH, dest)
    return community
