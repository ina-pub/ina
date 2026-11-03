from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, func, insert, select

from app.ids import Fullname, Kind
from app.repos.base import AdminAction
from app.repos.pg.tables import accounts, admin_log, communities

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class PgAdminRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self, admin_id: int, action: str, target: Fullname | None = None, details: str = ''
    ) -> None:
        await self.session.execute(
            insert(admin_log).values(
                admin_id=admin_id,
                action=action,
                target_type=target.kind.value if target else None,
                target_id=target.id if target else None,
                details=details,
            )
        )

    async def actions(self, *, limit: int, before_id: UUID | None = None) -> list[AdminAction]:
        admins = accounts.alias('admins')
        statement = (
            select(
                admin_log.c.id,
                admins.c.name.label('admin_name'),
                admin_log.c.action,
                admin_log.c.target_type,
                admin_log.c.target_id,
                func.coalesce(accounts.c.name, communities.c.name).label('target_name'),
                admin_log.c.details,
                admin_log.c.created,
            )
            .join(admins, admins.c.id == admin_log.c.admin_id)
            .outerjoin(
                accounts,
                and_(
                    admin_log.c.target_type == Kind.ACCOUNT.value,
                    accounts.c.id == admin_log.c.target_id,
                ),
            )
            .outerjoin(
                communities,
                and_(
                    admin_log.c.target_type == Kind.COMMUNITY.value,
                    communities.c.id == admin_log.c.target_id,
                ),
            )
            .order_by(admin_log.c.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            statement = statement.where(admin_log.c.id < before_id)
        return [
            AdminAction(
                id=row.id,
                admin_name=row.admin_name,
                action=row.action,
                target=Fullname(Kind(row.target_type), row.target_id) if row.target_type else None,
                target_name=row.target_name,
                details=row.details,
                created=row.created,
            )
            for row in await self.session.execute(statement)
        ]
