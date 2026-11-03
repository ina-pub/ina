from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, insert, select, update

from app.ids import Kind, to36
from app.repos.base import Comment, Sort, TreeRow
from app.repos.pg import ranking
from app.repos.pg.profiles import profile_page
from app.repos.pg.tables import accounts, comments, communities, links, votes

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement, Row, Select, Update
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repos.base import NewComment, ProfileQuery

COLUMNS = (
    comments.c.id,
    comments.c.link_id,
    comments.c.parent_id,
    func.ltree2text(comments.c.path).label('path'),
    comments.c.depth,
    comments.c.author_id,
    accounts.c.name.label('author_name'),
    links.c.community_id,
    communities.c.name.label('community_name'),
    comments.c.body_md,
    comments.c.body_html,
    comments.c.score,
    comments.c.upvotes,
    comments.c.downvotes,
    comments.c.confidence,
    comments.c.controversy_score,
    comments.c.created,
    comments.c.edited,
    comments.c.removed,
    comments.c.removed_by,
    comments.c.spam,
    comments.c.deleted,
    comments.c.distinguished,
    comments.c.sendreplies,
    comments.c.approved_by,
    comments.c.ignore_reports,
    comments.c.reported,
)
JOINED = (
    comments.join(accounts, accounts.c.id == comments.c.author_id)
    .join(links, links.c.id == comments.c.link_id)
    .join(communities, communities.c.id == links.c.community_id)
)


def to_comment(row: Row[*tuple[Any, ...]]) -> Comment:
    return Comment(**row._asdict())


class PgCommentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self) -> Select[Any]:
        return select(*COLUMNS).select_from(JOINED)

    async def create(self, comment: NewComment) -> Comment:
        # The id comes first so the path, which ends in it, is written with the row.
        sequence = func.nextval(func.pg_get_serial_sequence('comments', 'id'))
        comment_id = (await self.session.execute(select(sequence))).scalar_one()
        parent = comment.parent
        label = to36(comment_id)
        await self.session.execute(
            insert(comments).values(
                id=comment_id,
                link_id=comment.link_id,
                parent_id=parent.id if parent else None,
                path=func.text2ltree(f'{parent.path}.{label}' if parent else label),
                depth=parent.depth + 1 if parent else 0,
                author_id=comment.author_id,
                body_md=comment.body_md,
                body_html=comment.body_html,
                sendreplies=comment.sendreplies,
                ip=comment.ip,
                created=comment.created,
                upvotes=comment.upvotes,
                downvotes=comment.downvotes,
                score=comment.upvotes - comment.downvotes,
                confidence=comment.confidence,
                controversy_score=comment.controversy_score,
            )
        )
        created = await self.by_id(comment_id)
        assert created is not None  # noqa: S101 - just inserted in this transaction
        return created

    async def by_id(self, comment_id: int) -> Comment | None:
        statement = self._select().where(comments.c.id == comment_id)
        row = (await self.session.execute(statement)).first()
        return to_comment(row) if row else None

    async def by_author(self, query: ProfileQuery) -> list[Comment]:
        values = {
            # A comment's best order is its hot, as r2's profile read it.
            Sort.HOT: comments.c.confidence,
            Sort.TOP: comments.c.score,
            Sort.CONTROVERSIAL: comments.c.controversy_score,
        }
        statement = profile_page(self._select(), comments, query, values)
        return [to_comment(row) for row in await self.session.execute(statement)]

    async def by_ids(self, comment_ids: list[int]) -> dict[int, Comment]:
        if not comment_ids:
            return {}
        rows = await self.session.execute(self._select().where(comments.c.id.in_(comment_ids)))
        return {comment.id: comment for comment in map(to_comment, rows)}

    async def for_link(self, link_id: int) -> list[Comment]:
        statement = self._select().where(comments.c.link_id == link_id).order_by(comments.c.id)
        return [to_comment(row) for row in await self.session.execute(statement)]

    async def tree_rows(self, link_id: int) -> list[TreeRow]:
        statement = (
            select(
                comments.c.id,
                comments.c.parent_id,
                comments.c.score,
                comments.c.confidence,
                comments.c.controversy_score,
                func.extract('epoch', comments.c.created),
                comments.c.author_id,
                comments.c.distinguished,
            )
            .where(comments.c.link_id == link_id)
            .order_by(comments.c.id)
        )
        return [
            TreeRow(row[0], row[1], row[2], row[3], row[4], float(row[5]), row[6], row[7])
            for row in await self.session.execute(statement)
        ]

    def _recounted(self, condition: ColumnElement[bool]) -> Update:
        def count(direction: int) -> ColumnElement[int]:
            return (
                select(func.count())
                .where(
                    votes.c.thing_type == Kind.COMMENT.value,
                    votes.c.thing_id == comments.c.id,
                    votes.c.direction == direction,
                )
                .scalar_subquery()
            )

        ups, downs = count(1), count(-1)
        return (
            update(comments)
            .where(condition)
            .values(
                upvotes=ups,
                downvotes=downs,
                score=ups - downs,
                confidence=ranking.confidence(ups, downs),
                controversy_score=ranking.controversy(ups, downs),
            )
        )

    async def recount(self, comment_id: int) -> Comment | None:
        await self.session.execute(self._recounted(comments.c.id == comment_id))
        return await self.by_id(comment_id)

    async def rebuild_scores(self, first_id: int, last_id: int) -> int:
        statement = self._recounted(and_(comments.c.id >= first_id, comments.c.id <= last_id))
        result = await self.session.execute(statement)
        return int(getattr(result, 'rowcount', 0))

    async def max_id(self) -> int:
        value = (await self.session.execute(select(func.max(comments.c.id)))).scalar()
        return int(value or 0)

    async def edit(self, comment_id: int, body_md: str, body_html: str, at: datetime) -> None:
        await self.session.execute(
            update(comments)
            .where(comments.c.id == comment_id)
            .values(body_md=body_md, body_html=body_html, edited=at)
        )

    async def delete(self, comment_id: int) -> None:
        await self.session.execute(
            update(comments).where(comments.c.id == comment_id).values(deleted=True)
        )
