from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.views.base import Page

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON


@dataclass(frozen=True, kw_only=True)
class ErrorPage(Page):
    template: ClassVar[str] = 'error.html'
    has_json: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    errors: tuple[FieldError, ...]
    status: int

    @property
    def status_code(self) -> int:
        return self.status

    def to_json(self) -> JSON:
        return {'json': {'errors': [error.to_json() for error in self.errors]}}
