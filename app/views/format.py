"""Template filters for how old reddit wrote times and counts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.infra import timing

if TYPE_CHECKING:
    from datetime import datetime

    from jinja2 import Environment

    from app.infra.assets import Assets

UNITS = (
    ('year', 365 * 24 * 60 * 60),
    ('month', 30 * 24 * 60 * 60),
    ('day', 24 * 60 * 60),
    ('hour', 60 * 60),
    ('minute', 60),
)


def plural(count: int, word: str, many: str | None = None) -> str:
    """'1 comment', '2 comments'; ``many`` for irregular plurals ('reply', 'replies')."""
    return f'{count} {word if count == 1 else (many or word + "s")}'


def scores(score: int, likes: bool | None) -> tuple[int, int, int]:  # noqa: FBT001 - a template global
    """The score shown after a downvote, no vote and an upvote, as r2 drew all three.

    ``score`` already counts the viewer's own vote, so that comes off first.
    """
    base = score - {True: 1, False: -1, None: 0}[likes]
    return base - 1, base, base + 1


def age(then: datetime, now: datetime) -> str:
    """'3 hours ago', counted in the largest whole unit, as r2's timesince did."""
    seconds = int((now - then).total_seconds())
    for unit, size in UNITS:
        if seconds >= size:
            return f'{plural(seconds // size, unit)} ago'
    return 'just now'


def register(
    environment: Environment,
    assets: Assets,
    media_url: str = '/media',
    source_url: str = '',
    *,
    debug: bool = False,
) -> None:
    environment.filters['age'] = age
    environment.filters['plural'] = plural
    environment.globals['scores'] = scores
    # ``stylesheet()``: the current bundle's URL.
    environment.globals['stylesheet'] = lambda: assets.css.path
    # ``media(key)``: where browsers fetch a stored object.
    environment.globals['media'] = lambda key: f'{media_url.rstrip("/")}/{key}'
    environment.globals['source_url'] = source_url
    # The admin bar's: this request's recording, if any, and what the bar says of the server.
    environment.globals['timing'] = timing.current
    environment.globals['debug'] = debug
    environment.globals['commit'] = timing.commit
    environment.globals['host'] = timing.host
