from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis

from app.errors import ErrorCode, InaError
from app.infra.ratelimit import Bucket
from app.services.ratelimits import enforce, wait_text

if TYPE_CHECKING:
    from app.settings import Settings


@pytest.mark.parametrize(
    ('seconds', 'text'),
    [
        (0.2, '1 second'),
        (20, '20 seconds'),
        (60, '1 minute'),
        (61, '2 minutes'),
        (3600, '1 hour'),
        (7201, '3 hours'),
    ],
)
def test_wait_text(seconds: float, text: str) -> None:
    assert wait_text(seconds) == text


async def test_enforce_raises_once_the_bucket_is_empty(settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url)
    bucket = Bucket('test', capacity=2, period=60)
    await enforce(redis, bucket, 'Someone')
    await enforce(redis, bucket, 'someone')
    with pytest.raises(InaError) as caught:
        await enforce(redis, bucket, 'SOMEONE')
    await enforce(redis, bucket, 'someone-else')
    await redis.aclose()
    (error,) = caught.value.errors
    assert error.code is ErrorCode.RATELIMIT
    assert error.field == 'ratelimit'
    assert error.message == 'slow down a little; try again in 30 seconds'
