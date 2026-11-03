from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, false, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.repos.pg.communities import COLUMNS, to_community
from app.repos.pg.tables import communities, quarantine_optins, subscriptions

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repos.base import Community


class PgSubscriptionRepo:
    """Subscriptions, with ``communities.subscriber_count`` kept in the same transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _count(self, community_id: int, change: int) -> None:
        await self.session.execute(
            update(communities)
            .where(communities.c.id == community_id)
            .values(subscriber_count=communities.c.subscriber_count + change)
        )

    async def subscribe(self, account_id: int, community_id: int) -> bool:
        statement = (
            insert(subscriptions)
            .values(account_id=account_id, community_id=community_id)
            .on_conflict_do_nothing()
            .returning(subscriptions.c.account_id)
        )
        added = (await self.session.execute(statement)).first() is not None
        if added:
            await self._count(community_id, 1)
        return added

    async def unsubscribe(self, account_id: int, community_id: int) -> bool:
        statement = (
            delete(subscriptions)
            .where(
                subscriptions.c.account_id == account_id,
                subscriptions.c.community_id == community_id,
            )
            .returning(subscriptions.c.account_id)
        )
        removed = (await self.session.execute(statement)).first() is not None
        if removed:
            await self._count(community_id, -1)
        return removed

    async def subscribed_among(self, account_id: int, community_ids: list[int]) -> set[int]:
        if not community_ids:
            return set()
        statement = select(subscriptions.c.community_id).where(
            subscriptions.c.account_id == account_id,
            subscriptions.c.community_id.in_(community_ids),
        )
        return set((await self.session.execute(statement)).scalars())

    async def subscribed_ids(self, account_id: int, *, limit: int) -> list[int]:
        statement = (
            select(subscriptions.c.community_id)
            .join(communities, communities.c.id == subscriptions.c.community_id)
            .where(subscriptions.c.account_id == account_id, communities.c.banned == false())
            .order_by(subscriptions.c.community_id)
            .limit(limit)
        )
        return list((await self.session.execute(statement)).scalars())

    async def subscribed(
        self, account_id: int, *, limit: int, after: Community | None
    ) -> list[Community]:
        statement = (
            select(*COLUMNS)
            .join(subscriptions, subscriptions.c.community_id == communities.c.id)
            .where(subscriptions.c.account_id == account_id, communities.c.banned == false())
            .order_by(func.lower(communities.c.name))
        )
        if after is not None:
            statement = statement.where(func.lower(communities.c.name) > after.name.lower())
        rows = await self.session.execute(statement.limit(limit))
        return [to_community(row) for row in rows]

    async def opted_in(self, account_id: int, community_id: int) -> bool:
        statement = select(quarantine_optins.c.account_id).where(
            quarantine_optins.c.account_id == account_id,
            quarantine_optins.c.community_id == community_id,
        )
        return (await self.session.execute(statement)).first() is not None

    async def set_opt_in(self, account_id: int, community_id: int, *, opted_in: bool) -> None:
        if opted_in:
            statement = insert(quarantine_optins).values(
                account_id=account_id, community_id=community_id
            )
            await self.session.execute(statement.on_conflict_do_nothing())
            return
        await self.session.execute(
            delete(quarantine_optins).where(
                quarantine_optins.c.account_id == account_id,
                quarantine_optins.c.community_id == community_id,
            )
        )
