"""The ``scores`` queue: recount votes and rewrite score and ranking columns."""

from __future__ import annotations

import dramatiq

from app.ids import Fullname
from app.infra.cache import Cache
from app.jobs.context import redis, transaction
from app.jobs.search import index_link
from app.repos.pg.accounts import PgAccountRepo
from app.repos.pg.comments import PgCommentRepo
from app.repos.pg.links import PgLinkRepo
from app.services import scores


async def recount(link_fullname: str) -> None:
    async with transaction() as session:
        await scores.recount_link(
            PgLinkRepo(session),
            PgAccountRepo(session),
            Cache(redis()),
            Fullname.parse(link_fullname).id,
        )
    # Its score and ranking are in its search document too.
    index_link.send(link_fullname)


recount_link = dramatiq.actor(actor_name='recount_link', queue_name='scores')(recount)


async def recount_one_comment(comment_fullname: str) -> None:
    async with transaction() as session:
        link = await scores.recount_comment(
            PgCommentRepo(session),
            PgLinkRepo(session),
            PgAccountRepo(session),
            Cache(redis()),
            Fullname.parse(comment_fullname).id,
        )
    if link is not None:
        # The thread's comment count is in its search document.
        index_link.send(str(link.fullname))


recount_comment = dramatiq.actor(actor_name='recount_comment', queue_name='scores')(
    recount_one_comment
)
