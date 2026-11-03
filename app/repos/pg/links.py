from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Integer,
    and_,
    cast,
    exists,
    false,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    true,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT
from sqlalchemy.dialects.postgresql import insert as upsert

from app.ids import Fullname, Kind
from app.repos.base import Link, Sort
from app.repos.pg import ranking
from app.repos.pg.profiles import profile_page
from app.repos.pg.tables import accounts, comments, communities, links, votes

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy import ColumnElement, Row, Select, Update
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repos.base import Embed, ListingQuery, NewLink, ProfileQuery

COLUMNS = (
    links.c.id,
    links.c.community_id,
    communities.c.name.label('community_name'),
    links.c.author_id,
    accounts.c.name.label('author_name'),
    links.c.title,
    links.c.url,
    links.c.domain,
    links.c.is_self,
    links.c.selftext_md,
    links.c.selftext_html,
    links.c.nsfw,
    links.c.spoiler,
    links.c.flair_text,
    links.c.flair_css,
    links.c.score,
    links.c.upvotes,
    links.c.downvotes,
    links.c.hot_score,
    links.c.controversy_score,
    links.c.comment_count,
    links.c.created,
    links.c.edited,
    links.c.removed,
    links.c.removed_by,
    links.c.spam,
    links.c.deleted,
    links.c.locked,
    links.c.stickied,
    links.c.suggested_sort,
    links.c.contest_mode,
    links.c.sendreplies,
    links.c.sticky_comment_id,
    links.c.distinguished,
    links.c.approved_by,
    links.c.ignore_reports,
    links.c.reported,
    func.coalesce(links.c.media['thumbnail'].astext, '').label('thumbnail'),
    func.coalesce(links.c.media['thumbnail_width'].as_integer(), 0).label('thumbnail_width'),
    func.coalesce(links.c.media['thumbnail_height'].as_integer(), 0).label('thumbnail_height'),
    func.coalesce(links.c.media['embed']['provider'].astext, '').label('embed_provider'),
    func.coalesce(links.c.media['embed']['id'].astext, '').label('embed_id'),
    func.coalesce(links.c.media['embed']['start'].as_integer(), 0).label('embed_start'),
)
JOINED = links.join(communities, communities.c.id == links.c.community_id).join(
    accounts, accounts.c.id == links.c.author_id
)
# The column each sort orders by, before the id tiebreak; new orders by id alone.
SORT_COLUMNS = {
    Sort.HOT: links.c.hot_score,
    Sort.TOP: links.c.score,
    Sort.CONTROVERSIAL: links.c.controversy_score,
}
SORT_VALUES = {
    Sort.HOT: lambda link: link.hot_score,
    Sort.TOP: lambda link: link.score,
    Sort.CONTROVERSIAL: lambda link: link.controversy_score,
}


def to_link(row: Row[*tuple[Any, ...]]) -> Link:
    return Link(**row._asdict())


def _keys(query: ListingQuery) -> list[tuple[ColumnElement[Any], bool]]:
    """The columns the sort orders by, each with whether this page reads it descending.

    A page before an anchor reads the order backwards; the caller reverses the result.
    """
    descending = query.before is None
    if query.sort is Sort.NEW:
        return [(links.c.id, descending)]
    return [(SORT_COLUMNS[query.sort], descending), (links.c.id, descending)]


def _continues(query: ListingQuery) -> ColumnElement[bool] | None:
    """The condition that picks up the order past (or before) the anchor link."""
    anchor = query.before or query.after
    if anchor is None:
        return None
    position: ColumnElement[Any]
    value: tuple[float | int, ...]
    if query.sort is Sort.NEW:
        position, value = tuple_(links.c.id), (anchor.id,)
    else:
        position = tuple_(SORT_COLUMNS[query.sort], links.c.id)
        value = (SORT_VALUES[query.sort](anchor), anchor.id)
    return position > value if query.before is not None else position < value


def _ordered(keys: Sequence[tuple[ColumnElement[Any], bool]]) -> list[ColumnElement[Any]]:
    return [column.desc() if descending else column.asc() for column, descending in keys]


def _filters(query: ListingQuery) -> list[ColumnElement[bool]]:
    filters = [links.c.removed == false(), links.c.spam == false(), links.c.deleted == false()]
    if query.since is not None:
        filters.append(links.c.created >= query.since)
    if not query.include_nsfw:
        filters.append(links.c.nsfw == false())
    if query.min_score is not None:
        filters.append(links.c.score >= query.min_score)
    if query.domain is not None:
        filters.append(links.c.domain == query.domain)
    if query.author_ids is not None:
        filters.append(links.c.author_id.in_(query.author_ids))
    return filters


def _all_communities(query: ListingQuery) -> list[ColumnElement[bool]]:
    """r/all's communities: every public or restricted one that lets itself be listed."""
    return [
        communities.c.type != 'private',
        communities.c.banned == false(),
        communities.c.settings['allow_top'].as_boolean().is_not(false()),
        communities.c.settings['quarantine'].as_boolean().is_not(true()),
        or_(literal(query.include_nsfw), communities.c.nsfw == false()),
    ]


# r2 counted a rising link's age as at least 45 minutes, so a new link cannot lead on one vote.
RISING_MIN_AGE = 45 * 60


class PgLinkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self) -> Select[Any]:
        return select(*COLUMNS).select_from(JOINED)

    async def create(self, link: NewLink) -> Link:
        statement = insert(links).values(
            community_id=link.community_id,
            author_id=link.author_id,
            title=link.title,
            url=link.url,
            domain=link.domain,
            is_self=link.is_self,
            selftext_md=link.selftext_md,
            selftext_html=link.selftext_html,
            nsfw=link.nsfw,
            sendreplies=link.sendreplies,
            ip=link.ip,
            created=link.created,
            upvotes=link.upvotes,
            downvotes=link.downvotes,
            score=link.upvotes - link.downvotes,
            hot_score=link.hot_score,
            controversy_score=link.controversy_score,
        )
        link_id = (await self.session.execute(statement.returning(links.c.id))).scalar_one()
        created = await self.by_id(link_id)
        assert created is not None  # noqa: S101 - just inserted in this transaction
        return created

    async def by_id(self, link_id: int) -> Link | None:
        row = (await self.session.execute(self._select().where(links.c.id == link_id))).first()
        return to_link(row) if row else None

    async def by_author(self, query: ProfileQuery) -> list[Link]:
        values = {
            Sort.HOT: links.c.hot_score,
            Sort.TOP: links.c.score,
            Sort.CONTROVERSIAL: links.c.controversy_score,
        }
        statement = profile_page(self._select(), links, query, values)
        return [to_link(row) for row in await self.session.execute(statement)]

    async def set_thumbnail(self, link_id: int, key: str, size: tuple[int, int]) -> None:
        added = func.jsonb_build_object(
            'thumbnail', key, 'thumbnail_width', size[0], 'thumbnail_height', size[1]
        )
        await self.session.execute(
            update(links)
            .where(links.c.id == link_id)
            .values(media=func.coalesce(links.c.media, text("'{}'::jsonb")).op('||')(added))
        )

    async def set_embed(self, link_id: int, embed: Embed) -> None:
        added = func.jsonb_build_object(
            'embed',
            func.jsonb_build_object(
                'provider', embed.provider, 'id', embed.id, 'start', embed.start
            ),
        )
        await self.session.execute(
            update(links)
            .where(links.c.id == link_id)
            .values(media=func.coalesce(links.c.media, text("'{}'::jsonb")).op('||')(added))
        )

    async def by_ids(self, link_ids: list[int]) -> dict[int, Link]:
        if not link_ids:
            return {}
        rows = await self.session.execute(self._select().where(links.c.id.in_(link_ids)))
        return {link.id: link for link in map(to_link, rows)}

    async def by_url(self, community_id: int, url: str) -> Link | None:
        statement = self._select().where(
            links.c.community_id == community_id,
            links.c.url == url,
            links.c.deleted == false(),
            links.c.removed == false(),
        )
        row = (await self.session.execute(statement.limit(1))).first()
        return to_link(row) if row else None

    async def listing(self, query: ListingQuery) -> list[int]:
        keys = _keys(query)
        filters = _filters(query)
        if (continues := _continues(query)) is not None:
            filters.append(continues)
        if query.community_ids is None:
            statement = (
                select(links.c.id)
                .select_from(links.join(communities, communities.c.id == links.c.community_id))
                .where(*filters, *_all_communities(query))
                .order_by(*_ordered(keys))
                .limit(query.limit)
            )
            return list((await self.session.execute(statement)).scalars())
        # One index-ordered scan per community, each at most a page long, then merged. A
        # listing of several (the front page, a multi) leaves banned communities out; one
        # community's own page is only reached by those who may see it.
        listed = (
            func.unnest(cast(list(query.community_ids), ARRAY(BIGINT)))
            .table_valued('id')
            .render_derived(name='listed')
        )
        if len(query.community_ids) > 1:
            filters.append(
                ~exists().where(communities.c.id == links.c.community_id, communities.c.banned)
            )
        labels = [f'key{index}' for index in range(len(keys))]
        page = (
            select(
                links.c.id.label('link_id'),
                *(c.label(n) for (c, _), n in zip(keys, labels, strict=True)),
            )
            .where(links.c.community_id == listed.c.id, *filters)
            .order_by(*_ordered(keys))
            .limit(query.limit)
            .lateral('page')
        )
        merged = [
            (page.c[name], descending) for (_, descending), name in zip(keys, labels, strict=True)
        ]
        statement = (
            select(page.c.link_id)
            .select_from(listed.join(page, true()))
            .order_by(*_ordered(merged))
            .limit(query.limit)
        )
        return list((await self.session.execute(statement)).scalars())

    async def rising(self, query: ListingQuery) -> list[int]:
        age = func.greatest(
            func.extract('epoch', func.now() - links.c.created), literal(RISING_MIN_AGE)
        )
        statement = (
            select(links.c.id)
            .select_from(links.join(communities, communities.c.id == links.c.community_id))
            .where(*_filters(query))
        )
        if query.community_ids is None:
            statement = statement.where(*_all_communities(query))
        else:
            statement = statement.where(links.c.community_id.in_(query.community_ids))
            if len(query.community_ids) > 1:
                statement = statement.where(communities.c.banned == false())
        statement = statement.order_by((links.c.score / age).desc(), links.c.id.desc())
        return list((await self.session.execute(statement.limit(query.limit))).scalars())

    async def same_url(self, url: str, *, exclude: int, limit: int) -> list[Link]:
        statement = (
            self._select()
            .where(
                links.c.url == url,
                links.c.id != exclude,
                communities.c.banned == false(),
                links.c.is_self == false(),
                links.c.removed == false(),
                links.c.spam == false(),
                links.c.deleted == false(),
            )
            .order_by(links.c.id.desc())
            .limit(limit)
        )
        return [to_link(row) for row in await self.session.execute(statement)]

    def _recounted(self, condition: ColumnElement[bool]) -> Update:
        def count(direction: int) -> ColumnElement[int]:
            return (
                select(func.count())
                .where(
                    votes.c.thing_type == Kind.LINK.value,
                    votes.c.thing_id == links.c.id,
                    votes.c.direction == direction,
                )
                .scalar_subquery()
            )

        ups, downs = count(1), count(-1)
        return (
            update(links)
            .where(condition)
            .values(
                upvotes=ups,
                downvotes=downs,
                score=ups - downs,
                hot_score=ranking.hot(ups, downs, links.c.created),
                controversy_score=ranking.controversy(ups, downs),
            )
        )

    async def recount(self, link_id: int) -> Link | None:
        await self.session.execute(self._recounted(links.c.id == link_id))
        return await self.by_id(link_id)

    async def rebuild_scores(self, first_id: int, last_id: int) -> int:
        statement = self._recounted(and_(links.c.id >= first_id, links.c.id <= last_id))
        result = await self.session.execute(statement)
        return int(getattr(result, 'rowcount', 0))

    async def max_id(self) -> int:
        value = (await self.session.execute(select(func.max(links.c.id)))).scalar()
        return int(value or 0)

    async def recount_comments(self, link_id: int) -> Link | None:
        live = (
            select(func.count())
            .where(comments.c.link_id == link_id, comments.c.deleted == false())
            .scalar_subquery()
        )
        await self.session.execute(
            update(links).where(links.c.id == link_id).values(comment_count=live)
        )
        return await self.by_id(link_id)

    async def edit(self, link_id: int, selftext_md: str, selftext_html: str, at: datetime) -> None:
        await self.session.execute(
            update(links)
            .where(links.c.id == link_id)
            .values(selftext_md=selftext_md, selftext_html=selftext_html, edited=at)
        )

    async def delete(self, link_id: int) -> None:
        await self.session.execute(update(links).where(links.c.id == link_id).values(deleted=True))


class PgVoteRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cast(self, account_id: int, thing: Fullname, direction: int) -> None:
        statement = upsert(votes).values(
            account_id=account_id,
            thing_type=thing.kind.value,
            thing_id=thing.id,
            direction=direction,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[votes.c.account_id, votes.c.thing_type, votes.c.thing_id],
            set_={'direction': statement.excluded.direction, 'created': func.now()},
        )
        await self.session.execute(statement)

    async def voted_links(
        self, account_id: int, direction: int, *, limit: int, before: datetime | None
    ) -> list[tuple[int, datetime]]:
        statement = select(votes.c.thing_id, votes.c.created).where(
            votes.c.account_id == account_id,
            votes.c.thing_type == Kind.LINK.value,
            votes.c.direction == direction,
        )
        if before is not None:
            statement = statement.where(votes.c.created < before)
        statement = statement.order_by(votes.c.created.desc()).limit(limit)
        return [(row.thing_id, row.created) for row in await self.session.execute(statement)]

    async def voted_at(self, account_id: int, thing: Fullname) -> datetime | None:
        statement = select(votes.c.created).where(
            votes.c.account_id == account_id,
            votes.c.thing_type == thing.kind.value,
            votes.c.thing_id == thing.id,
        )
        return (await self.session.execute(statement)).scalar()

    async def directions(self, account_id: int, kind: Kind, thing_ids: list[int]) -> dict[int, int]:
        if not thing_ids:
            return {}
        statement = select(votes.c.thing_id, cast(votes.c.direction, Integer)).where(
            votes.c.account_id == account_id,
            votes.c.thing_type == kind.value,
            votes.c.thing_id.in_(thing_ids),
            votes.c.direction != 0,
        )
        return {row[0]: row[1] for row in await self.session.execute(statement)}
