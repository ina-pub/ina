from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as upsert

from app.repos.base import FlairKind, FlairTemplate, TooManyTemplatesError, UserFlair
from app.repos.pg.tables import accounts, flair_templates, links, user_flair

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import Row, Select
    from sqlalchemy.ext.asyncio import AsyncSession

MAX_TEMPLATES = 350
FLAIR_COLUMNS = (
    user_flair.c.account_id,
    accounts.c.name,
    user_flair.c.text,
    user_flair.c.css_class,
    user_flair.c.enabled,
)
TEMPLATE_COLUMNS = (
    flair_templates.c.id,
    flair_templates.c.kind,
    flair_templates.c.text,
    flair_templates.c.css_class,
    flair_templates.c.text_editable,
    flair_templates.c.position,
)


def _flairs() -> Select[*tuple[Any, ...]]:
    return select(user_flair.c.community_id, *FLAIR_COLUMNS).join(
        accounts, accounts.c.id == user_flair.c.account_id
    )


def to_flair(row: Row[*tuple[Any, ...]]) -> UserFlair:
    return UserFlair(
        account_id=row.account_id,
        name=row.name,
        text=row.text,
        css_class=row.css_class,
        enabled=row.enabled,
    )


def to_template(row: Row[*tuple[Any, ...]]) -> FlairTemplate:
    return FlairTemplate(
        id=row.id,
        kind=FlairKind(row.kind),
        text=row.text,
        css_class=row.css_class,
        text_editable=row.text_editable,
        position=row.position,
    )


class PgFlairRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def user_flair(self, community_id: int, account_id: int) -> UserFlair | None:
        statement = _flairs().where(
            user_flair.c.community_id == community_id, user_flair.c.account_id == account_id
        )
        row = (await self.session.execute(statement)).first()
        return to_flair(row) if row else None

    async def user_flairs(self, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], UserFlair]:
        if not pairs:
            return {}
        statement = _flairs().where(
            tuple_(user_flair.c.community_id, user_flair.c.account_id).in_(sorted(set(pairs)))
        )
        rows = await self.session.execute(statement)
        return {(row.community_id, row.account_id): to_flair(row) for row in rows}

    async def flair_list(
        self, community_id: int, *, limit: int, after: str | None, name: str | None
    ) -> list[UserFlair]:
        statement = _flairs().where(
            user_flair.c.community_id == community_id,
            or_(user_flair.c.text.is_not(None), user_flair.c.css_class.is_not(None)),
        )
        if name is not None:
            statement = statement.where(func.lower(accounts.c.name) == name.lower())
        if after is not None:
            statement = statement.where(func.lower(accounts.c.name) > after.lower())
        statement = statement.order_by(func.lower(accounts.c.name)).limit(limit)
        return [to_flair(row) for row in await self.session.execute(statement)]

    async def set_user_flair(
        self, community_id: int, account_id: int, *, text: str | None, css_class: str | None
    ) -> None:
        now = datetime.now(UTC)
        statement = upsert(user_flair).values(
            community_id=community_id,
            account_id=account_id,
            text=text,
            css_class=css_class,
            updated=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[user_flair.c.community_id, user_flair.c.account_id],
            set_={'text': text, 'css_class': css_class, 'updated': now},
        )
        await self.session.execute(statement)

    async def set_flair_enabled(self, community_id: int, account_id: int, *, enabled: bool) -> None:
        statement = upsert(user_flair).values(
            community_id=community_id, account_id=account_id, enabled=enabled
        )
        statement = statement.on_conflict_do_update(
            index_elements=[user_flair.c.community_id, user_flair.c.account_id],
            set_={'enabled': enabled},
        )
        await self.session.execute(statement)

    async def templates(self, community_id: int, kind: FlairKind) -> list[FlairTemplate]:
        statement = (
            select(*TEMPLATE_COLUMNS)
            .where(flair_templates.c.community_id == community_id, flair_templates.c.kind == kind)
            .order_by(flair_templates.c.position)
        )
        return [to_template(row) for row in await self.session.execute(statement)]

    async def template(self, community_id: int, template_id: UUID) -> FlairTemplate | None:
        statement = select(*TEMPLATE_COLUMNS).where(
            flair_templates.c.community_id == community_id, flair_templates.c.id == template_id
        )
        row = (await self.session.execute(statement)).first()
        return to_template(row) if row else None

    async def save_template(
        self,
        community_id: int,
        kind: FlairKind,
        *,
        template_id: UUID | None,
        text: str,
        css_class: str,
        text_editable: bool,
    ) -> FlairTemplate:
        values = {'text': text, 'css_class': css_class, 'text_editable': text_editable}
        if template_id is not None:
            statement = (
                update(flair_templates)
                .where(
                    flair_templates.c.community_id == community_id,
                    flair_templates.c.id == template_id,
                )
                .values(**values)
                .returning(*TEMPLATE_COLUMNS)
            )
            row = (await self.session.execute(statement)).first()
            if row is not None:
                return to_template(row)
        count, last = (
            await self.session.execute(
                select(func.count(), func.max(flair_templates.c.position)).where(
                    flair_templates.c.community_id == community_id, flair_templates.c.kind == kind
                )
            )
        ).one()
        if count >= MAX_TEMPLATES:
            raise TooManyTemplatesError
        insert = (
            flair_templates.insert()
            .values(community_id=community_id, kind=kind, position=(last or 0) + 1, **values)
            .returning(*TEMPLATE_COLUMNS)
        )
        return to_template((await self.session.execute(insert)).one())

    async def delete_template(self, community_id: int, template_id: UUID) -> bool:
        statement = (
            delete(flair_templates)
            .where(
                flair_templates.c.community_id == community_id, flair_templates.c.id == template_id
            )
            .returning(flair_templates.c.id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def clear_templates(self, community_id: int, kind: FlairKind) -> None:
        await self.session.execute(
            delete(flair_templates).where(
                flair_templates.c.community_id == community_id, flair_templates.c.kind == kind
            )
        )

    async def set_link_flair(
        self, link_id: int, *, text: str | None, css_class: str | None
    ) -> None:
        await self.session.execute(
            update(links).where(links.c.id == link_id).values(flair_text=text, flair_css=css_class)
        )
