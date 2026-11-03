"""Multis: a multi's listing with its sidebar, and r2's LabeledMulti JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from litestar.status_codes import HTTP_200_OK, HTTP_201_CREATED

from app.views.base import Page, View

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Multi
    from app.views.links import Listing


def multi_json(multi: Multi, *, can_edit: bool) -> JSON:
    """r2's LabeledMulti."""
    created = multi.created.timestamp()
    return {
        'kind': 'LabeledMulti',
        'data': {
            'can_edit': can_edit,
            'copied_from': multi.copied_from if can_edit else None,
            'created': created,
            'created_utc': created,
            'description_html': multi.description_html,
            'description_md': multi.description_md,
            'display_name': multi.display_name or multi.name,
            'icon_url': None,
            'key_color': multi.key_color,
            'name': multi.name,
            'path': multi.path,
            'communities': [{'name': name} for _, name in multi.communities],
            'visibility': multi.visibility,
            'weighting_scheme': multi.weighting_scheme,
        },
    }


@dataclass(frozen=True, kw_only=True)
class MultiPage(Page):
    """``/user/{name}/m/{multi}``, or ``/in/a+b`` for communities joined on the spot."""

    template: ClassVar[str] = 'multi.html'
    has_json: ClassVar[bool] = True

    listing: Listing
    # A user's multi; None for /in/a+b.
    multi: Multi | None = None
    # The communities in it, by name, that the viewer can see.
    names: tuple[str, ...] = ()
    can_edit: bool = False
    errors: tuple[FieldError, ...] = ()
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    visibilities: ClassVar[tuple[str, ...]] = ('private', 'public', 'hidden')

    @property
    def title(self) -> str:
        if self.multi is None:
            return '+'.join(self.names)
        return self.multi.display_name or self.multi.name

    def to_json(self) -> JSON:
        return self.listing.to_json(self.viewer.modhash if self.viewer else '')


@dataclass(frozen=True, kw_only=True)
class MultiJson(View):
    """``/api/multi/...``: one multi."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    multi: Multi
    can_edit: bool
    created: bool = False

    @property
    def status_code(self) -> int:
        # r2 answered a new multi with 201 Created.
        return HTTP_201_CREATED if self.created else HTTP_200_OK

    def to_json(self) -> JSON:
        return multi_json(self.multi, can_edit=self.can_edit)


@dataclass(frozen=True, kw_only=True)
class MultiList(View):
    """``/api/multi/mine`` and ``/api/multi/user/{name}``."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    multis: tuple[Multi, ...]
    can_edit: bool

    def to_json(self) -> JSON:
        return [multi_json(multi, can_edit=self.can_edit) for multi in self.multis]


@dataclass(frozen=True, kw_only=True)
class Plain(View):
    """A small JSON answer, such as ``{}`` for a DELETE or ``{"name": ...}``, as r2 sent them."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    data: dict[str, JSON] = field(default_factory=dict)

    def to_json(self) -> JSON:
        return self.data
