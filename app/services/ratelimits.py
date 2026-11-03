"""The rate limits the site enforces, and the RATELIMIT error they raise."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from app.errors import ErrorCode, InaError
from app.infra import ratelimit
from app.infra.ratelimit import Bucket

if TYPE_CHECKING:
    from redis.asyncio import Redis

LOGIN_BY_IP = Bucket('login-ip', capacity=10, period=60)
LOGIN_BY_NAME = Bucket('login-name', capacity=6, period=300)
REGISTER_BY_IP = Bucket('register-ip', capacity=3, period=600)
PASSWORD_RESET_BY_IP = Bucket('password-ip', capacity=5, period=3600)
CREATE_COMMUNITY_BY_ACCOUNT = Bucket('create-community', capacity=5, period=86400)
SUBMIT_BY_ACCOUNT = Bucket('submit', capacity=10, period=3600)
COMMENT_BY_ACCOUNT = Bucket('comment', capacity=30, period=600)
VOTE_BY_ACCOUNT = Bucket('vote', capacity=120, period=60)
MESSAGE_BY_ACCOUNT = Bucket('message', capacity=10, period=600)
REPORT_BY_ACCOUNT = Bucket('report', capacity=20, period=600)
# Requests an API app makes with its tokens, for each account it acts for (and for itself).
API_BY_APP = Bucket('api', capacity=60, period=60)

MINUTE = 60
HOUR = 60 * MINUTE


def wait_text(seconds: float) -> str:
    """Say how long to wait the way r2 did: '3 minutes', '1 hour', '20 seconds'."""
    for unit, size in (('hour', HOUR), ('minute', MINUTE)):
        if seconds >= size:
            count = math.ceil(seconds / size)
            return f'{count} {unit}{"s" if count != 1 else ""}'
    count = max(1, math.ceil(seconds))
    return f'{count} second{"s" if count != 1 else ""}'


async def enforce(redis: Redis, bucket: Bucket, subject: str) -> None:
    taken = await ratelimit.take(redis, bucket, subject.lower())
    if not taken.allowed:
        raise InaError.of(ErrorCode.RATELIMIT, 'ratelimit', time=wait_text(taken.retry_after))
