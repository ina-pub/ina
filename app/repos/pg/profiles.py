"""What links and comments share on a profile: which of them show, in what order, from where."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import false, tuple_

from app.repos.base import Sort
from app.repos.pg.tables import links

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement, Select, Table

    from app.repos.base import ProfileQuery


def profile_page(
    statement: Select[*tuple[Any, ...]], table: Table, query: ProfileQuery, values: dict[Sort, Any]
) -> Select[*tuple[Any, ...]]:
    """Filter and order a profile's links or comments; ``values`` names each sort's column."""
    conditions: list[ColumnElement[bool]] = [
        table.c.author_id == query.author_id,
        table.c.deleted == false(),
    ]
    if not query.include_removed:
        conditions += [table.c.removed == false(), table.c.spam == false()]
    if query.since is not None:
        conditions.append(table.c.created >= query.since)
    if query.excluded:
        # Comments carry their community through their link, which every query here joins.
        conditions.append(links.c.community_id.not_in(query.excluded))
    if query.sort is Sort.NEW:
        order = [table.c.created.desc(), table.c.id.desc()]
        if query.after is not None:
            conditions.append(table.c.created < query.after.created)
    else:
        value = values[query.sort]
        order = [value.desc(), table.c.created.desc(), table.c.id.desc()]
        if query.after is not None:
            position = tuple_(value, table.c.created)
            conditions.append(position < (query.after.value, query.after.created))
    return statement.where(*conditions).order_by(*order).limit(query.limit)
