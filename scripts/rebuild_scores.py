"""Recount every link's and comment's votes and rewrite their score and ranking columns.

    uv run python -m scripts.rebuild_scores [--things links,comments] [--from ID] [--batch SIZE]

Each batch of ids commits on its own and prints the last id done, so a stopped run resumes with
``--from`` that id plus one. Cached listings expire within a minute and need no flush.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infra import db
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.links import PgLinkRepo
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_BATCH = 1000


class Rebuildable(Protocol):
    async def max_id(self) -> int: ...

    async def rebuild_scores(self, first_id: int, last_id: int) -> int: ...


REPOS: dict[str, Callable[[AsyncSession], Rebuildable]] = {
    'links': PgLinkRepo,
    'comments': PgCommentRepo,
}


async def rebuild(
    start: int = 1, batch: int = DEFAULT_BATCH, things: tuple[str, ...] = ('links', 'comments')
) -> int:
    """Rebuild each kind of thing from id ``start`` up; return how many rows were rewritten."""
    engine = db.create_engine(get_settings())
    sessions = async_sessionmaker(engine)
    total = 0
    try:
        for thing in things:
            repo = REPOS[thing]
            async with sessions() as session:
                last = await repo(session).max_id()
            for first in range(start, last + 1, batch):
                through = min(first + batch - 1, last)
                async with sessions() as session, session.begin():
                    total += await repo(session).rebuild_scores(first, through)
                sys.stdout.write(f'{thing}: done through id {through} ({total} rows)\n')
    finally:
        await engine.dispose()
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--things', default='links,comments', help='comma-separated: links,comments'
    )
    parser.add_argument('--from', dest='start', type=int, default=1)
    parser.add_argument('--batch', type=int, default=DEFAULT_BATCH)
    arguments = parser.parse_args()
    things = tuple(name for name in arguments.things.split(',') if name in REPOS)
    asyncio.run(rebuild(arguments.start, arguments.batch, things))


if __name__ == '__main__':
    main()
