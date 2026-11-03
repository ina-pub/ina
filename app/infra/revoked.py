"""Revoked session ids, held until the session would have expired anyway."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.infra.redis import key

if TYPE_CHECKING:
    from redis.asyncio import Redis

REVOKED = key('sess', 'revoked')


async def revoke(redis: Redis, session_id: str, *, expires_at: float) -> None:
    """Revoke one session. The set is scored by expiry so stale ids can be pruned."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zadd(REVOKED, {session_id: expires_at})
        pipe.zremrangebyscore(REVOKED, '-inf', time.time())
        await pipe.execute()


async def is_revoked(redis: Redis, session_id: str) -> bool:
    return await redis.zscore(REVOKED, session_id) is not None
