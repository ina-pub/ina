"""Token buckets in Redis, keyed by scope (``login``, ``register``) and by account or IP."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from app.infra.redis import key

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from redis.asyncio import Redis

# Refill, then take one token if there is one. Runs atomically, on Redis's clock.
TAKE = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local time = redis.call('TIME')
local now = tonumber(time[1]) + tonumber(time[2]) / 1000000
local state = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(state[1]) or capacity
local at = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + (now - at) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate))
return {allowed, tostring(retry), tostring(tokens)}
"""


@dataclass(frozen=True, slots=True)
class Bucket:
    """``capacity`` requests at once, refilled at ``capacity`` per ``period`` seconds."""

    scope: str
    capacity: int
    period: float

    @property
    def rate(self) -> float:
        return self.capacity / self.period


@dataclass(frozen=True, slots=True)
class Taken:
    allowed: bool
    retry_after: float
    remaining: int
    # Seconds until the bucket is full again.
    refilled_in: float


async def take(redis: Redis, bucket: Bucket, subject: str) -> Taken:
    result = await cast(
        'Awaitable[list[bytes | int]]',
        redis.eval(TAKE, 1, key('rl', bucket.scope, subject), bucket.capacity, bucket.rate),
    )
    allowed, retry, tokens = result
    left = float(tokens)
    return Taken(
        allowed=bool(allowed),
        retry_after=float(retry),
        remaining=math.floor(left),
        refilled_in=(bucket.capacity - left) / bucket.rate,
    )
