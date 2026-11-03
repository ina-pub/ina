from __future__ import annotations

from litestar import get

from app.controllers.deps import DbSessionDep
from app.services import health
from app.views.health import Check, HealthView


@get('/health', cache=False)
async def show_health(db_session: DbSessionDep) -> HealthView:
    report = await health.check(db_session)
    return HealthView(checks=(Check(name='postgres', ok=report.postgres),))
