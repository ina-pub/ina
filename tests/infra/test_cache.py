from __future__ import annotations

from typing import TYPE_CHECKING

from redis.asyncio import Redis

from app.infra.cache import Cache

if TYPE_CHECKING:
    from app.settings import Settings


async def test_get_or_load_loads_once(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    cache = Cache(redis)
    calls = []

    async def load() -> list[int]:
        calls.append(1)
        return [3, 1, 2]

    assert await cache.get_or_load('x', load, type_=list[int], ttl=60) == [3, 1, 2]
    assert await cache.get_or_load('x', load, type_=list[int], ttl=60) == [3, 1, 2]
    assert calls == [1]
    await redis.aclose()


async def test_things_loads_only_misses_and_backfills(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    cache = Cache(redis)
    asked: list[list[int]] = []

    async def load(ids: list[int]) -> dict[int, str]:
        asked.append(ids)
        return {i: f'thing{i}' for i in ids if i != 99}

    names = {1: 't3_1', 2: 't3_2', 99: 't3_2r'}
    assert await cache.things(names, load, type_=str) == {1: 'thing1', 2: 'thing2'}
    assert await cache.things({1: 't3_1', 3: 't3_3'}, load, type_=str) == {1: 'thing1', 3: 'thing3'}
    assert asked == [[1, 2, 99], [3]]
    await cache.forget_things(['t3_1'])
    await cache.things({1: 't3_1'}, load, type_=str)
    assert asked[-1] == [1]
    assert await cache.things({}, load, type_=str) == {}
    await redis.aclose()


async def test_bump_moves_the_generation(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    cache = Cache(redis)
    assert await cache.generation('community:1') == 0
    await cache.bump('community:1')
    await cache.bump('community:1')
    assert await cache.generation('community:1') == 2
    assert await cache.generation('community:2') == 0
    await redis.aclose()


async def test_values_of_an_old_shape_are_reloaded(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    cache = Cache(redis)
    await cache.put('x', {'old': 'shape'}, ttl=60)

    async def load() -> list[int]:
        return [1]

    assert await cache.get_or_load('x', load, type_=list[int], ttl=60) == [1]
    await cache.put('thing:t3_1', 'not a number', ttl=60)

    async def load_things(ids: list[int]) -> dict[int, int]:
        return dict.fromkeys(ids, 7)

    assert await cache.things({1: 't3_1'}, load_things, type_=int) == {1: 7}
    await redis.aclose()
