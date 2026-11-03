"""Community traffic, as r2's /about/traffic showed it.

Page views and unique visitors by hour, day and month, and new subscriptions by day. r2 counted
these from its request logs into Postgres; this counts them in Redis as pages are served, a
counter and a HyperLogLog of visitors for each period, each kept only as long as the page shows
it. A visitor is the account, or for someone logged out a hash of their address and browser, as
r2 counted uniques; the address itself is never stored.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis


@dataclass(frozen=True, slots=True)
class Period:
    name: str
    # How many of these the page shows, newest first.
    shown: int
    # How long its counts are kept: a little more than it is shown.
    keep: timedelta

    def start(self, at: datetime) -> datetime:
        at = at.astimezone(UTC)
        if self.name == 'hour':
            return at.replace(minute=0, second=0, microsecond=0)
        if self.name == 'day':
            return at.replace(hour=0, minute=0, second=0, microsecond=0)
        return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def previous(self, start: datetime) -> datetime:
        if self.name == 'hour':
            return start - timedelta(hours=1)
        if self.name == 'day':
            return start - timedelta(days=1)
        return (start - timedelta(days=1)).replace(day=1)


HOUR = Period('hour', 72, timedelta(days=4))
DAY = Period('day', 60, timedelta(days=62))
MONTH = Period('month', 12, timedelta(days=400))
PERIODS = (HOUR, DAY, MONTH)


def _key(community_id: int, period: Period, start: datetime, what: str) -> str:
    return f'traffic:{community_id}:{period.name}:{start:%Y%m%d%H}:{what}'


def visitor(account_id: int | None, ip: str, user_agent: str) -> str:
    if account_id is not None:
        return f'a{account_id}'
    return hashlib.sha256(f'{ip}\n{user_agent}'.encode()).hexdigest()[:24]


async def record_view(
    redis: Redis, community_id: int, who: str, *, now: datetime | None = None
) -> None:
    now = now or datetime.now(UTC)
    async with redis.pipeline(transaction=False) as pipe:
        for period in PERIODS:
            start = period.start(now)
            views, uniques = (_key(community_id, period, start, w) for w in ('pv', 'uv'))
            ttl = int(period.keep.total_seconds())
            pipe.incr(views)
            pipe.expire(views, ttl)
            pipe.pfadd(uniques, who)
            pipe.expire(uniques, ttl)
        await pipe.execute()


async def record_subscription(
    redis: Redis, community_id: int, *, now: datetime | None = None
) -> None:
    """A new subscription; r2 counted these, and could not count unsubscriptions."""
    start = DAY.start(now or datetime.now(UTC))
    key = _key(community_id, DAY, start, 'subs')
    async with redis.pipeline(transaction=False) as pipe:
        pipe.incr(key)
        pipe.expire(key, int(DAY.keep.total_seconds()))
        await pipe.execute()


@dataclass(frozen=True, slots=True)
class Row:
    start: datetime
    uniques: int
    pageviews: int
    subscriptions: int


async def history(
    redis: Redis, community_id: int, period: Period, *, now: datetime | None = None
) -> list[Row]:
    """The last ``period.shown`` periods, newest first, the current one included."""
    starts = [period.start(now or datetime.now(UTC))]
    while len(starts) < period.shown:
        starts.append(period.previous(starts[-1]))
    async with redis.pipeline(transaction=False) as pipe:
        for start in starts:
            pipe.pfcount(_key(community_id, period, start, 'uv'))
            pipe.get(_key(community_id, period, start, 'pv'))
            pipe.get(_key(community_id, period, start, 'subs'))
        found = await pipe.execute()
    return [
        Row(start, int(found[i * 3]), int(found[i * 3 + 1] or 0), int(found[i * 3 + 2] or 0))
        for i, start in enumerate(starts)
    ]
