"""r2's interstitial pages, shown before a page that needs consent or an opt-in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from litestar.status_codes import HTTP_200_OK, HTTP_403_FORBIDDEN

from app.views.base import Page


@dataclass(frozen=True, kw_only=True)
class InterstitialPage(Page):
    """``/over18`` or ``/quarantine``: ask first, then go on to ``dest``."""

    template: ClassVar[str] = 'interstitial.html'

    # over18 or quarantine.
    kind: str
    dest: str
    community_name: str = ''
    # Logged in with a verified email, as r2 required to opt in to quarantine.
    can_opt_in: bool = False

    @property
    def title(self) -> str:
        return 'over 18?' if self.kind == 'over18' else 'quarantined'

    @property
    def status_code(self) -> int:
        """r2 answered its quarantine page with a 403."""
        return HTTP_403_FORBIDDEN if self.kind == 'quarantine' else HTTP_200_OK
