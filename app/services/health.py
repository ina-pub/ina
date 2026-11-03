"""Whether the stores a request depends on are reachable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.infra import db

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class HealthReport:
    postgres: bool

    @property
    def ok(self) -> bool:
        return self.postgres


async def check(session: AsyncSession) -> HealthReport:
    return HealthReport(postgres=await db.ping(session))
