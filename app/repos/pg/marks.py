from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as upsert

from app.ids import Fullname, Kind
from app.repos.pg.tables import marks

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repos.base import Mark


class PgMarkRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, account_id: int, mark: Mark, thing: Fullname) -> None:
        statement = upsert(marks).values(
            account_id=account_id, mark=mark.value, thing_type=thing.kind.value, thing_id=thing.id
        )
        await self.session.execute(statement.on_conflict_do_nothing())

    async def remove(self, account_id: int, mark: Mark, thing: Fullname) -> None:
        await self.session.execute(
            delete(marks).where(
                marks.c.account_id == account_id,
                marks.c.mark == mark.value,
                marks.c.thing_type == thing.kind.value,
                marks.c.thing_id == thing.id,
            )
        )

    async def among(self, account_id: int, mark: Mark, things: list[Fullname]) -> set[Fullname]:
        if not things:
            return set()
        statement = select(marks.c.thing_type, marks.c.thing_id).where(
            marks.c.account_id == account_id,
            marks.c.mark == mark.value,
            tuple_(marks.c.thing_type, marks.c.thing_id).in_(
                [(thing.kind.value, thing.id) for thing in things]
            ),
        )
        rows = await self.session.execute(statement)
        return {Fullname(Kind(row.thing_type), row.thing_id) for row in rows}

    async def page(
        self, account_id: int, mark: Mark, *, limit: int, before: datetime | None
    ) -> list[Fullname]:
        statement = select(marks.c.thing_type, marks.c.thing_id).where(
            marks.c.account_id == account_id, marks.c.mark == mark.value
        )
        if before is not None:
            statement = statement.where(marks.c.created < before)
        statement = statement.order_by(marks.c.created.desc()).limit(limit)
        rows = await self.session.execute(statement)
        return [Fullname(Kind(row.thing_type), row.thing_id) for row in rows]

    async def marked_at(self, account_id: int, mark: Mark, thing: Fullname) -> datetime | None:
        statement = select(marks.c.created).where(
            marks.c.account_id == account_id,
            marks.c.mark == mark.value,
            marks.c.thing_type == thing.kind.value,
            marks.c.thing_id == thing.id,
        )
        return (await self.session.execute(statement)).scalar()
