"""The site admins' pages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from app.views.base import Page

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.repos.base import Account, AdminAction, Community
    from app.views.moderation import QueueItem

# /admin's tabs, each with its heading.
TABS = {
    'reports': 'reported anywhere on the site',
    'accounts': 'newest accounts',
    'communities': 'newest communities',
    'log': 'what the admins did',
}


@dataclass(frozen=True, kw_only=True)
class AdminOnPage(Page):
    """``/admin/on``: the password again, before admin mode turns on."""

    template: ClassVar[str] = 'admin_on.html'
    cacheable: ClassVar[bool] = False

    dest: str = '/'
    errors: tuple[FieldError, ...] = ()

    @property
    def title(self) -> str:
        return 'admin mode'


@dataclass(frozen=True, kw_only=True)
class AdminPage(Page):
    """``/admin`` and its tabs: what an admin looks after across the site."""

    template: ClassVar[str] = 'admin.html'
    cacheable: ClassVar[bool] = False
    tabs: ClassVar[dict[str, str]] = TABS

    tab: str
    items: tuple[QueueItem, ...] = ()
    accounts: tuple[Account, ...] = ()
    communities: tuple[Community, ...] = ()
    actions: tuple[AdminAction, ...] = ()
    # Where the next page starts, if there is one.
    after: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def title(self) -> str:
        return f'admin: {self.tab}'
