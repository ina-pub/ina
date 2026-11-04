"""Cache-aside in Redis. Nothing here is authoritative; every value can be reloaded.

Values are msgspec JSON under versioned keys (see ``app.infra.redis.key``). Listings are cached
under a per-scope generation, so invalidating one is an INCR rather than a key scan. A value that
no longer decodes into its type, because the type changed, counts as a miss and is reloaded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

from app.infra.redis import key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from redis.asyncio import Redis

THING_TTL = 60 * 60
LISTING_TTL = 60


def _decode[T](value: bytes | str | None, type_: type[T]) -> T | None:
    if value is None:
        return None
    try:
        return msgspec.json.decode(value, type=type_)
    except msgspec.DecodeError:
        return None


class Cache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def get_or_load[T](
        self, name: str, loader: Callable[[], Awaitable[T]], *, type_: type[T], ttl: int
    ) -> T:
        cached = _decode(await self.redis.get(key(name)), type_)
        if cached is not None:
            return cached
        value = await loader()
        await self.redis.set(key(name), msgspec.json.encode(value), ex=ttl)
        return value

    async def put(self, name: str, value: object, *, ttl: int) -> None:
        await self.redis.set(key(name), msgspec.json.encode(value), ex=ttl)

    async def things[T](
        self,
        fullnames: dict[int, str],
        loader: Callable[[list[int]], Awaitable[dict[int, T]]],
        *,
        type_: type[T],
    ) -> dict[int, T]:
        """Many things by id: one MGET, one load for the misses, then backfill."""
        if not fullnames:
            return {}
        ids = list(fullnames)
        cached = await self.redis.mget([key('thing', fullnames[i]) for i in ids])
        decoded = {
            thing_id: _decode(value, type_) for thing_id, value in zip(ids, cached, strict=True)
        }
        found = {thing_id: value for thing_id, value in decoded.items() if value is not None}
        if missing := [thing_id for thing_id in ids if thing_id not in found]:
            loaded = await loader(missing)
            async with self.redis.pipeline(transaction=False) as pipe:
                for thing_id, value in loaded.items():
                    pipe.set(
                        key('thing', fullnames[thing_id]), msgspec.json.encode(value), ex=THING_TTL
                    )
                await pipe.execute()
            found.update(loaded)
        return found

    async def forget_things(self, fullnames: Iterable[str]) -> None:
        if names := [key('thing', fullname) for fullname in fullnames]:
            await self.redis.delete(*names)

    async def generation(self, scope: str) -> int:
        value = await self.redis.get(key('listing', scope, 'gen'))
        return int(value or 0)

    async def bump(self, scope: str) -> None:
        """Retire every cached listing in ``scope``; the old keys expire on their own."""
        await self.redis.incr(key('listing', scope, 'gen'))
