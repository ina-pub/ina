"""What a page shows beside its links and comments: flair, thumbnails, saved and hidden."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ids import Fullname
from app.repos.base import Mark, Prefs
from app.services import embeds
from app.services import flair as flair_service
from app.services.relations import FRIEND
from app.views.links import LinkRow, Thumb

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from app.repos.base import (
        AccountRepo,
        Comment,
        Community,
        CommunityRepo,
        FlairRepo,
        Link,
        MarkRepo,
    )
    from app.services.flair import FlairDisplay
    from app.services.sessions import Authenticated


@dataclass(frozen=True, slots=True)
class PageDetails:
    """What a page shows beside its links: flair, thumbnails, and what the viewer saved or hid."""

    display: FlairDisplay
    prefs: Prefs
    media_url: str
    # This site's host name, which Twitch's player must be told.
    site_host: str
    saved: frozenset[Fullname] = frozenset()
    hidden: frozenset[Fullname] = frozenset()
    # Authors on the page who are the viewer's friends.
    friends: frozenset[int] = frozenset()

    def thumbnail(self, link: Link) -> Thumb:
        """r2's choice: the image where the viewer's and community's settings allow it."""
        if link.is_self:
            return Thumb('self')
        if link.nsfw and self.prefs.label_nsfw:
            return Thumb('nsfw')
        community = self.display.communities.get(link.community_id)
        allowed = self.prefs.media == 'on' or (
            self.prefs.media == 'community'
            and community is not None
            and community.settings.show_media
        )
        if not link.thumbnail or not allowed:
            return Thumb('default')
        url = f'{self.media_url.rstrip("/")}/{link.thumbnail}'
        return Thumb('image', url, link.thumbnail_width, link.thumbnail_height)

    def row(self, link: Link, rank: int | None, directions: dict[int, int]) -> LinkRow:
        embed = link.embed
        return LinkRow(
            link,
            rank,
            {1: True, -1: False}.get(directions.get(link.id, 0)),
            self.display.author(link.community_id, link.author_id),
            self.display.link(link),
            self.thumbnail(link),
            saved=link.fullname in self.saved,
            hidden=link.fullname in self.hidden,
            friend=link.author_id in self.friends,
            player=embeds.player(embed, self.site_host) if embed else None,
            over_18=self.prefs.over_18,
        )


@dataclass(frozen=True, slots=True)
class Details:
    """Looks up what a page shows beside its things, by the communities and the viewer's prefs."""

    flair: FlairRepo
    communities: CommunityRepo
    marks: MarkRepo
    accounts: AccountRepo
    media_url: str = '/media'
    site_host: str = 'localhost'

    async def of(
        self,
        links: list[Link],
        viewer: Authenticated | None,
        comments: Iterable[Comment] = (),
        *,
        known: Sequence[Community] = (),
    ) -> PageDetails:
        prefs = viewer.account.prefs if viewer else Prefs()
        authors = [(link.community_id, link.author_id) for link in links]
        authors += [(comment.community_id, comment.author_id) for comment in comments]
        # Communities decide thumbnails as well as flair, so load every link's.
        known_ids = {community.id for community in known}
        extra = sorted({link.community_id for link in links} - known_ids)
        known = [*known, *(await self.communities.by_ids(extra)).values()]
        shown = await flair_service.display(
            self.flair, self.communities, prefs, authors, known=known
        )
        if viewer is None:
            return PageDetails(shown, prefs, self.media_url, self.site_host)
        link_names = [link.fullname for link in links]
        names = link_names + [comment.fullname for comment in comments]
        account_id = viewer.account.id
        author_ids = sorted({author_id for _, author_id in authors})
        return PageDetails(
            shown,
            prefs,
            self.media_url,
            self.site_host,
            frozenset(await self.marks.among(account_id, Mark.SAVED, names)),
            frozenset(await self.marks.among(account_id, Mark.HIDDEN, link_names)),
            frozenset(await self.accounts.related_among(account_id, FRIEND, author_ids)),
        )
