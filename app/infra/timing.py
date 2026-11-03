"""Where a request's time went, for the admin bar, as r2's admin bar and Peek showed it.

A request is recorded only when the bar will be shown: in admin mode, or when the site runs in
debug. The recording lives in a context variable for the request; Postgres statements (through
SQLAlchemy's cursor events), Redis commands, OpenSearch and object storage requests, and
rendering each add a span to it. Statements are kept without their bound values, and Redis
commands by name alone, so a recording holds no one's data.

Once the response starts, the totals go out as a ``Server-Timing`` header, and a page's recording
is kept in Redis for a minute under its request id, where the bar's script fetches it.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
from sqlalchemy import event

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine

# What the spans are of, in the order the bar lists them, with the names it shows.
KINDS = {
    'pg': 'Postgres',
    'redis': 'Redis',
    'search': 'OpenSearch',
    'storage': 'storage',
    'render': 'rendering',
}
# How long a page's recording waits in Redis for the bar to fetch it.
KEPT_SECONDS = 60
ROOT = Path(__file__).parent.parent.parent


class Span(msgspec.Struct, frozen=True):
    kind: str
    label: str
    # Milliseconds from the start of the request.
    start: float
    duration: float


@dataclass(slots=True)
class Recording:
    id: str
    started: float = field(default_factory=time.perf_counter)
    spans: list[Span] = field(default_factory=list)
    # Milliseconds until the response started; None until then.
    total: float | None = None

    def add(self, kind: str, label: str, began: float) -> None:
        now = time.perf_counter()
        self.spans.append(Span(kind, label, (began - self.started) * 1000, (now - began) * 1000))

    def finish(self) -> None:
        self.total = (time.perf_counter() - self.started) * 1000

    def totals(self) -> dict[str, tuple[int, float]]:
        """Each kind's count and milliseconds, for the kinds there were any of."""
        found: dict[str, tuple[int, float]] = {}
        for span in self.spans:
            count, spent = found.get(span.kind, (0, 0.0))
            found[span.kind] = (count + 1, spent + span.duration)
        return {kind: found[kind] for kind in KINDS if kind in found}

    def server_timing(self) -> str:
        """The ``Server-Timing`` header: the total, then each kind's time and count."""
        parts = [f'total;dur={self.total or 0:.1f}']
        for kind, (count, spent) in self.totals().items():
            parts.append(f'{kind};desc="{KINDS[kind]}, {count}";dur={spent:.1f}')
        return ', '.join(parts)

    def to_json(self) -> bytes:
        return msgspec.json.encode(
            {
                'id': self.id,
                'total': self.total,
                'totals': {
                    kind: {'name': KINDS[kind], 'count': count, 'ms': spent}
                    for kind, (count, spent) in self.totals().items()
                },
                'spans': self.spans,
            }
        )


CURRENT: ContextVar[Recording | None] = ContextVar('timing', default=None)


def current() -> Recording | None:
    return CURRENT.get()


@contextmanager
def timed(kind: str, label: str) -> Iterator[None]:
    """Record what runs inside as a span, if this request is being recorded."""
    recording = CURRENT.get()
    if recording is None:
        yield
        return
    began = time.perf_counter()
    try:
        yield
    finally:
        recording.add(kind, label, began)


STARTED = 'ina_timing_started'


def record_statements(engine: AsyncEngine) -> None:
    """Time each statement the engine runs, while a request is being recorded."""

    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def before(
        _connection: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        context: object,
        _executemany: bool,  # noqa: FBT001 - SQLAlchemy passes it positionally
    ) -> None:
        if CURRENT.get() is not None and context is not None:
            setattr(context, STARTED, time.perf_counter())

    @event.listens_for(engine.sync_engine, 'after_cursor_execute')
    def after(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        context: object,
        _executemany: bool,  # noqa: FBT001 - SQLAlchemy passes it positionally
    ) -> None:
        recording = CURRENT.get()
        began = getattr(context, STARTED, None)
        if recording is not None and began is not None:
            recording.add('pg', statement, began)


@cache
def commit() -> str:
    """The commit this process runs: INA_COMMIT, as a deploy sets it, or git's HEAD."""
    if found := os.environ.get('INA_COMMIT'):
        return found[:12]
    try:
        head = subprocess.run(
            ['git', 'rev-parse', '--short=12', 'HEAD'],  # noqa: S607 - git from PATH
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return head.stdout.strip() or 'unknown'


@cache
def host() -> str:
    return socket.gethostname()
