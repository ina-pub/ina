"""The page where someone lets an app in, and the scope list."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from litestar.status_codes import HTTP_200_OK, HTTP_400_BAD_REQUEST

from app.services.oauth import SCOPES
from app.views.base import Page, View

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.jsontypes import JSON
    from app.repos.base import OAuthClient
    from app.services.oauth import Scope


@dataclass(frozen=True, kw_only=True)
class AuthorizePage(Page):
    """``/api/v1/authorize``: what the app asks for, to allow or decline."""

    template: ClassVar[str] = 'authorize.html'
    cacheable: ClassVar[bool] = False

    client: OAuthClient | None = None
    scopes: tuple[Scope, ...] = ()
    permanent: bool = False
    # The request as the app sent it, posted back with the answer.
    params: Mapping[str, str] | None = None
    # Why the request can't go back to the app.
    problem: str | None = None

    @property
    def status_code(self) -> int:
        return HTTP_400_BAD_REQUEST if self.problem else HTTP_200_OK

    @property
    def title(self) -> str:
        return 'let an app in'


@dataclass(frozen=True, kw_only=True)
class ScopesJson(View):
    """``/api/v1/scopes``: each scope's id, name and description."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True

    scopes: tuple[Scope, ...] = tuple(SCOPES.values())

    def to_json(self) -> JSON:
        return {
            scope.id: {'id': scope.id, 'name': scope.name, 'description': scope.description}
            for scope in self.scopes
        }
