"""The answer to a form post, shaped for both the site's JS and browsers without it."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

from app.views.base import View

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.errors import FieldError
    from app.jsontypes import JSON


@dataclass(frozen=True, kw_only=True)
class FormResult(View):
    """JSON gets ``{json: {errors, data}}``, as r2's ``api_type=json`` did.

    Without JS the browser follows ``redirect``, or, when there are errors and a ``page``, sees
    that page again with the errors marked on it. A ``page`` with no ``redirect`` is always shown.
    """

    template: ClassVar[str] = ''
    cacheable: ClassVar[bool] = False

    errors: tuple[FieldError, ...] = ()
    data: Mapping[str, JSON] = field(default_factory=lambda: MappingProxyType({}))
    redirect: str | None = None
    page: View | None = None

    def to_json(self) -> JSON:
        body: dict[str, JSON] = {'errors': [error.to_json() for error in self.errors]}
        if self.data:
            body['data'] = dict(self.data)
        return {'json': body}
