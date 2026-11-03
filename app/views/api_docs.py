"""``/dev/api``: the API reference, as a page and as an OpenAPI document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.services.oauth import SCOPES
from app.views.base import Page, View

if TYPE_CHECKING:
    from litestar.handlers import HTTPRouteHandler

    from app.jsontypes import JSON
    from app.services.oauth import Scope


@dataclass(frozen=True, slots=True, kw_only=True)
class Endpoint:
    method: str
    # As OpenAPI writes it, {name}; ``raw_path`` as the router has it, {name:str}.
    path: str
    raw_path: str
    handler: HTTPRouteHandler
    # Any one of these will do; none means any token.
    scopes: tuple[str, ...]
    summary: str


@dataclass(frozen=True, kw_only=True)
class ApiPage(Page):
    template: ClassVar[str] = 'dev_api.html'

    endpoints: tuple[Endpoint, ...]

    @property
    def title(self) -> str:
        return 'API'

    @property
    def by_scope(self) -> list[tuple[Scope, list[Endpoint]]]:
        """Each scope, with the routes that need it, in the order scopes are listed."""
        groups: dict[str, list[Endpoint]] = {}
        for endpoint in self.endpoints:
            for scope in endpoint.scopes:
                groups.setdefault(scope, []).append(endpoint)
        return [(scope, groups[scope.id]) for scope in SCOPES.values() if scope.id in groups]


@dataclass(frozen=True, kw_only=True)
class OpenApiJson(View):
    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True

    document: JSON

    def to_json(self) -> JSON:
        return self.document
