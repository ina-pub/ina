"""``/prefs/apps``: the apps an account registered, and the apps it let in."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.repos.base import AppKind
from app.services.oauth import SCOPES
from app.views.base import Page

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.repos.base import Authorization, OAuthClient
    from app.services.oauth import Scope

KIND_LABELS = {
    AppKind.WEB: 'web app: runs on a server and keeps a secret',
    AppKind.INSTALLED: "installed app: runs on someone's phone or computer",
    AppKind.SCRIPT: 'script: acts as you, for your own use',
}


@dataclass(frozen=True, kw_only=True)
class AppsPage(Page):
    template: ClassVar[str] = 'prefs_apps.html'
    cacheable: ClassVar[bool] = False

    apps: tuple[OAuthClient, ...]
    authorized: tuple[Authorization, ...]
    # The app whose secret was just made, with the secret; shown this once.
    secret: tuple[str, str] | None = None
    # Which app the errors are about: a client_id, or '' for the new-app form.
    editing: str | None = None
    errors: tuple[FieldError, ...] = ()

    kinds: ClassVar[tuple[tuple[str, str], ...]] = tuple(
        (kind.value, label) for kind, label in KIND_LABELS.items()
    )

    @property
    def title(self) -> str:
        return 'apps'

    def scopes(self, ids: frozenset[str]) -> list[Scope]:
        return [scope for scope in SCOPES.values() if scope.id in ids]
