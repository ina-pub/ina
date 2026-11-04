from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import IntegrityError

from app.repos.base import Multi, MultiExistsError
from app.repos.pg.tables import accounts, communities, multi_communities, multis

if TYPE_CHECKING:
    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repos.base import MultiFields

COLUMNS = (
    multis.c.id,
    multis.c.owner_id,
    accounts.c.name.label('owner_name'),
    multis.c.name,
    multis.c.display_name,
    multis.c.description_md,
    multis.c.description_html,
    multis.c.visibility,
    multis.c.key_color,
    multis.c.weighting_scheme,
    multis.c.copied_from,
    multis.c.created,
)


def _fields(fields: MultiFields) -> dict[str, str]:
    return {
        'display_name': fields.display_name,
        'description_md': fields.description_md,
        'description_html': fields.description_html,
        'visibility': fields.visibility,
        'key_color': fields.key_color,
        'weighting_scheme': fields.weighting_scheme,
    }


class PgMultiRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _load(self, statement: Select[*tuple[Any, ...]]) -> list[Multi]:
        rows = (await self.session.execute(statement)).all()
        if not rows:
            return []
        members = await self.session.execute(
            select(multi_communities.c.multi_id, communities.c.id, communities.c.name)
            .join(communities, communities.c.id == multi_communities.c.community_id)
            .where(multi_communities.c.multi_id.in_([row.id for row in rows]))
            .order_by(func.lower(communities.c.name))
        )
        by_multi: dict[int, list[tuple[int, str]]] = {}
        for multi_id, community_id, name in members:
            by_multi.setdefault(multi_id, []).append((community_id, name))
        return [Multi(**row._asdict(), communities=tuple(by_multi.get(row.id, []))) for row in rows]

    def _select(self) -> Select[*tuple[Any, ...]]:
        return select(*COLUMNS).join(accounts, accounts.c.id == multis.c.owner_id)

    async def by_path(self, owner: str, name: str) -> Multi | None:
        statement = self._select().where(
            func.lower(accounts.c.name) == owner.lower(), func.lower(multis.c.name) == name.lower()
        )
        found = await self._load(statement)
        return found[0] if found else None

    async def by_owner(self, owner_id: int) -> list[Multi]:
        statement = (
            self._select().where(multis.c.owner_id == owner_id).order_by(func.lower(multis.c.name))
        )
        return await self._load(statement)

    async def create(
        self, owner_id: int, name: str, fields: MultiFields, *, copied_from: str | None = None
    ) -> int:
        statement = (
            multis.insert()
            .values(owner_id=owner_id, name=name, copied_from=copied_from, **_fields(fields))
            .returning(multis.c.id)
        )
        try:
            async with self.session.begin_nested():
                return int((await self.session.execute(statement)).scalar_one())
        except IntegrityError as error:
            raise MultiExistsError(name) from error

    async def update(self, multi_id: int, fields: MultiFields) -> None:
        await self.session.execute(
            update(multis).where(multis.c.id == multi_id).values(**_fields(fields))
        )

    async def set_communities(self, multi_id: int, community_ids: list[int]) -> None:
        await self.session.execute(
            delete(multi_communities).where(multi_communities.c.multi_id == multi_id)
        )
        for community_id in dict.fromkeys(community_ids):
            await self.add_community(multi_id, community_id)

    async def add_community(self, multi_id: int, community_id: int) -> None:
        await self.session.execute(
            upsert(multi_communities)
            .values(multi_id=multi_id, community_id=community_id)
            .on_conflict_do_nothing()
        )

    async def remove_community(self, multi_id: int, community_id: int) -> None:
        await self.session.execute(
            delete(multi_communities).where(
                multi_communities.c.multi_id == multi_id,
                multi_communities.c.community_id == community_id,
            )
        )

    async def delete(self, multi_id: int) -> None:
        await self.session.execute(delete(multis).where(multis.c.id == multi_id))
