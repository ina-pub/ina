from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.jsontypes import JSON


def prefixed(community: str | None) -> str | None:
    """A community's name as people write it, for the ``*_name_prefixed`` fields."""
    return f'in/{community}' if community else None


def upvote_ratio(upvotes: int, downvotes: int) -> float:
    total = upvotes + downvotes
    return round(upvotes / total, 2) if total else 0.0


def listing_json(
    children: list[JSON], *, after: str | None, before: str | None, modhash: str
) -> JSON:
    """Reddit's ``Listing`` envelope."""
    return {
        'kind': 'Listing',
        'data': {
            'modhash': modhash,
            'dist': len(children),
            'children': children,
            'after': after,
            'before': before,
        },
    }
