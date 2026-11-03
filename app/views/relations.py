"""``/prefs/friends`` and ``/prefs/blocked``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.ids import Fullname, Kind
from app.views.base import Page

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Member


@dataclass(frozen=True, kw_only=True)
class RelationsPage(Page):
    template: ClassVar[str] = 'prefs_relations.html'
    has_json: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    # friend or enemy.
    kind: str
    members: tuple[Member, ...]
    errors: tuple[FieldError, ...] = ()

    @property
    def title(self) -> str:
        return 'friends' if self.kind == 'friend' else 'blocked users'

    def to_json(self) -> JSON:
        """r2's ``UserList``."""
        children: list[JSON] = [
            {
                'name': m.name,
                'id': str(Fullname(Kind.ACCOUNT, m.account_id)),
                'date': m.created.timestamp(),
            }
            for m in self.members
        ]
        return {'kind': 'UserList', 'data': {'children': children}}
