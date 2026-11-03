"""Work that must wait until the request's transaction has committed.

The request's database session commits when the handler's dependencies are cleaned up, before
the response is sent; background tasks run after it is sent. So a job published from a
background task always finds the rows the request wrote.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from litestar.background_tasks import BackgroundTask

if TYPE_CHECKING:
    from dramatiq import Actor


def publish(actor: Actor[Any, Any], *args: str) -> BackgroundTask:
    """Publish a message once the response has gone; publishing is a blocking network call."""
    return BackgroundTask(asyncio.to_thread, actor.send, *args)
