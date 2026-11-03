"""The Redis client and the key scheme. Nothing in Redis is authoritative."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline

from app.infra.timing import timed

if TYPE_CHECKING:
    from app.settings import Settings

# Bump when the shape of any stored value changes, instead of flushing.
KEY_VERSION = 'v3'


class TimedRedis(Redis):
    """A client whose commands the admin bar times, by command name only."""

    @override
    async def execute_command(self, *args: Any, **options: Any) -> Any:
        with timed('redis', str(args[0]) if args else '?'):
            return await super().execute_command(*args, **options)  # type: ignore[no-untyped-call]

    @override
    def pipeline(self, transaction: bool = True, shard_hint: str | None = None) -> Pipeline:
        return TimedPipeline(self.connection_pool, self.response_callbacks, transaction, shard_hint)


class TimedPipeline(Pipeline):
    @override
    async def execute(self, raise_on_error: bool = True) -> list[Any]:
        with timed('redis', f'pipeline of {len(self.command_stack)}'):
            return await super().execute(raise_on_error)


def create_redis(settings: Settings) -> Redis:
    return TimedRedis.from_url(settings.redis_url)


def key(*parts: str) -> str:
    return ':'.join((KEY_VERSION, *parts))
