"""The site stylesheet: every file in static/css, in name order, minified into one bundle.

It is served at ``/assets/site.<hash>.css`` with a year-long cache, so a change gets a new URL.
The bundle is rebuilt when a file in static/css changes, which costs a few stats per page.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.text.css import minify

if TYPE_CHECKING:
    from pathlib import Path

PREFIX = '/assets/'


@dataclass(frozen=True, slots=True)
class Bundle:
    name: str
    body: bytes
    # The newest source file's change time, and how many there were, to notice edits.
    stamp: float
    files: int

    @property
    def path(self) -> str:
        return f'{PREFIX}{self.name}'


def _sources(directory: Path) -> list[Path]:
    return sorted(directory.glob('*.css'))


def build(directory: Path) -> Bundle:
    sources = _sources(directory)
    css = '\n'.join(path.read_text() for path in sources)
    body = minify(css).encode()
    digest = hashlib.sha256(body).hexdigest()[:12]
    stamp = max((path.stat().st_mtime for path in sources), default=0.0)
    return Bundle(name=f'site.{digest}.css', body=body, stamp=stamp, files=len(sources))


class Assets:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._bundle = build(directory)

    @property
    def css(self) -> Bundle:
        sources = _sources(self.directory)
        stamp = max((path.stat().st_mtime for path in sources), default=0.0)
        if stamp != self._bundle.stamp or len(sources) != self._bundle.files:
            self._bundle = build(self.directory)
        return self._bundle
