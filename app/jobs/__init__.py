"""Dramatiq actors. ``dramatiq app.jobs`` imports this package and runs every actor in it.

Messages carry fullnames, never objects; each actor reloads what it needs and is safe to replay.
"""

from __future__ import annotations

# The broker first: an actor binds to whichever broker is set when it is defined.
from app.jobs import broker  # isort: split

from app.jobs import bouncer, inbox, mail, media, scores, search, trees

__all__ = ['bouncer', 'broker', 'inbox', 'mail', 'media', 'scores', 'search', 'trees']
