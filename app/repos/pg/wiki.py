from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert as upsert

from app.repos.base import Member, WikiConflictError, WikiPage, WikiRevision
from app.repos.pg.tables import accounts, wiki_editors, wiki_pages, wiki_revisions

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import Row, Select
    from sqlalchemy.ext.asyncio import AsyncSession

_reviser = accounts.alias('reviser')
PAGE_COLUMNS = (
    wiki_pages.c.id,
    wiki_pages.c.community_id,
    wiki_pages.c.name,
    wiki_pages.c.permlevel,
    wiki_pages.c.listed,
    wiki_pages.c.content_md,
    wiki_pages.c.content_html,
    wiki_pages.c.revision_id,
    _reviser.c.name.label('revised_by'),
    wiki_pages.c.revised_at,
)
REVISION_COLUMNS = (
    wiki_revisions.c.id,
    wiki_revisions.c.page_id,
    wiki_pages.c.name.label('page_name'),
    wiki_revisions.c.author_id,
    accounts.c.name.label('author_name'),
    wiki_revisions.c.content_md,
    wiki_revisions.c.reason,
    wiki_revisions.c.hidden,
    wiki_revisions.c.created,
)


def _pages() -> Select[*tuple[Any, ...]]:
    return select(*PAGE_COLUMNS).outerjoin(_reviser, _reviser.c.id == wiki_pages.c.revised_by)


def _revisions() -> Select[*tuple[Any, ...]]:
    return (
        select(*REVISION_COLUMNS)
        .join(wiki_pages, wiki_pages.c.id == wiki_revisions.c.page_id)
        .outerjoin(accounts, accounts.c.id == wiki_revisions.c.author_id)
    )


def to_page(row: Row[Any]) -> WikiPage:
    return WikiPage(**row._asdict())


def to_revision(row: Row[Any]) -> WikiRevision:
    return WikiRevision(**row._asdict())


class PgWikiRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _page(self, statement: Select[*tuple[Any, ...]]) -> WikiPage | None:
        row = (await self.session.execute(statement)).first()
        return to_page(row) if row else None

    async def page(self, community_id: int, name: str) -> WikiPage | None:
        return await self._page(
            _pages().where(wiki_pages.c.community_id == community_id, wiki_pages.c.name == name)
        )

    async def pages(self, community_id: int) -> list[WikiPage]:
        statement = (
            _pages().where(wiki_pages.c.community_id == community_id).order_by(wiki_pages.c.name)
        )
        return [to_page(row) for row in await self.session.execute(statement)]

    async def create_page(self, community_id: int, name: str) -> WikiPage:
        await self.session.execute(
            upsert(wiki_pages)
            .values(community_id=community_id, name=name)
            .on_conflict_do_nothing(index_elements=[wiki_pages.c.community_id, wiki_pages.c.name])
        )
        page = await self.page(community_id, name)
        assert page is not None  # noqa: S101 - just inserted, or already there
        return page

    async def revise(
        self,
        page_id: int,
        *,
        content_md: str,
        content_html: str,
        author_id: int,
        reason: str,
        previous: UUID | None,
    ) -> WikiRevision:
        # The row lock makes the check and the write one step for concurrent edits.
        locked = await self._page(
            _pages().where(wiki_pages.c.id == page_id).with_for_update(of=wiki_pages)
        )
        if locked is None:
            msg = f'no wiki page {page_id}'
            raise LookupError(msg)
        if previous is not None and previous != locked.revision_id:
            raise WikiConflictError(locked)
        now = datetime.now(UTC)
        revision_id = (
            await self.session.execute(
                wiki_revisions.insert()
                .values(
                    page_id=page_id,
                    author_id=author_id,
                    content_md=content_md,
                    reason=reason,
                    created=now,
                )
                .returning(wiki_revisions.c.id)
            )
        ).scalar_one()
        await self.session.execute(
            update(wiki_pages)
            .where(wiki_pages.c.id == page_id)
            .values(
                content_md=content_md,
                content_html=content_html,
                revision_id=revision_id,
                revised_by=author_id,
                revised_at=now,
            )
        )
        revision = await self.revision(page_id, revision_id)
        assert revision is not None  # noqa: S101 - just inserted
        return revision

    async def revision(self, page_id: int, revision_id: UUID) -> WikiRevision | None:
        statement = _revisions().where(
            wiki_revisions.c.page_id == page_id, wiki_revisions.c.id == revision_id
        )
        row = (await self.session.execute(statement)).first()
        return to_revision(row) if row else None

    async def revisions(
        self,
        community_id: int,
        *,
        page_id: int | None,
        limit: int,
        before: UUID | None,
        include_hidden: bool,
    ) -> list[WikiRevision]:
        statement = _revisions().where(wiki_pages.c.community_id == community_id)
        if page_id is not None:
            statement = statement.where(wiki_revisions.c.page_id == page_id)
        if before is not None:
            statement = statement.where(wiki_revisions.c.id < before)
        if not include_hidden:
            statement = statement.where(wiki_revisions.c.hidden.is_(False))
        statement = statement.order_by(wiki_revisions.c.id.desc()).limit(limit)
        return [to_revision(row) for row in await self.session.execute(statement)]

    async def set_hidden(self, revision_id: UUID, *, hidden: bool) -> None:
        await self.session.execute(
            update(wiki_revisions).where(wiki_revisions.c.id == revision_id).values(hidden=hidden)
        )

    async def set_page_settings(self, page_id: int, *, permlevel: int, listed: bool) -> None:
        await self.session.execute(
            update(wiki_pages)
            .where(wiki_pages.c.id == page_id)
            .values(permlevel=permlevel, listed=listed)
        )

    async def editors(self, page_id: int) -> list[Member]:
        statement = (
            select(wiki_editors.c.account_id, accounts.c.name, wiki_editors.c.created)
            .join(accounts, accounts.c.id == wiki_editors.c.account_id)
            .where(wiki_editors.c.page_id == page_id)
            .order_by(accounts.c.name)
        )
        return [Member(**row._asdict()) for row in await self.session.execute(statement)]

    async def is_editor(self, page_id: int, account_id: int) -> bool:
        statement = select(
            exists().where(
                wiki_editors.c.page_id == page_id, wiki_editors.c.account_id == account_id
            )
        )
        return bool((await self.session.execute(statement)).scalar())

    async def add_editor(self, page_id: int, account_id: int) -> None:
        await self.session.execute(
            upsert(wiki_editors)
            .values(page_id=page_id, account_id=account_id)
            .on_conflict_do_nothing()
        )

    async def remove_editor(self, page_id: int, account_id: int) -> None:
        await self.session.execute(
            delete(wiki_editors).where(
                wiki_editors.c.page_id == page_id, wiki_editors.c.account_id == account_id
            )
        )
