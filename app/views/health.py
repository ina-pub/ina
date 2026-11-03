from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from litestar.status_codes import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from app.views.base import View

if TYPE_CHECKING:
    from app.jsontypes import JSON


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool


@dataclass(frozen=True, slots=True)
class HealthView(View):
    template: ClassVar[str] = 'health.html'
    cacheable: ClassVar[bool] = False

    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def status_code(self) -> int:
        return HTTP_200_OK if self.ok else HTTP_503_SERVICE_UNAVAILABLE

    def to_json(self) -> JSON:
        return {
            'status': 'ok' if self.ok else 'degraded',
            'checks': {check.name: 'ok' if check.ok else 'failed' for check in self.checks},
        }
