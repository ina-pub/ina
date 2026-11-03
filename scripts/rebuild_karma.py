"""Recount every account's link and comment karma from the scores of what it posted.

    uv run python -m scripts.rebuild_karma [--from ID] [--batch SIZE]

Votes keep karma current as they come in; this is for data loaded some other way, such as the
seed script's, and after changing how karma is counted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.tables import accounts
from app.settings import get_settings

DEFAULT_BATCH = 500


async def rebuild(start: int = 1, batch: int = DEFAULT_BATCH) -> int:
    """Recount from account id ``start`` up; return how many accounts were recounted."""
    engine = db.create_engine(get_settings())
    sessions = async_sessionmaker(engine)
    done = 0
    try:
        async with sessions() as session:
            last = (await session.execute(select(func.max(accounts.c.id)))).scalar() or 0
        for first in range(start, last + 1, batch):
            through = min(first + batch - 1, last)
            async with sessions() as session, session.begin():
                ids = (
                    await session.execute(
                        select(accounts.c.id).where(accounts.c.id.between(first, through))
                    )
                ).scalars()
                repo = PgAccountRepo(session)
                for account_id in ids:
                    await repo.recount_karma(account_id)
                    done += 1
            sys.stdout.write(f'karma: done through id {through} ({done} accounts)\n')
    finally:
        await engine.dispose()
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--from', dest='start', type=int, default=1)
    parser.add_argument('--batch', type=int, default=DEFAULT_BATCH)
    arguments = parser.parse_args()
    asyncio.run(rebuild(arguments.start, arguments.batch))


if __name__ == '__main__':
    main()
