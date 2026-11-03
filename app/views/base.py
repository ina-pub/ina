"""The view contract: every response is a dataclass that renders as a template or as JSON."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from litestar.status_codes import HTTP_200_OK

if TYPE_CHECKING:
    from app.jsontypes import JSON


class View(ABC):
    """Everything a template or serializer needs, with no database access.

    The template receives the view as ``view``; ``to_json`` returns the API shape.
    """

    template: ClassVar[str]
    # False for site pages that have no API form; their ``.json`` is a 404.
    has_json: ClassVar[bool] = True
    # True for API responses that are JSON whatever the request asked for.
    json_only: ClassVar[bool] = False
    # Whether a logged-out GET of this view may be cached at the edge.
    cacheable: ClassVar[bool] = True

    @property
    def status_code(self) -> int:
        return HTTP_200_OK

    @abstractmethod
    def to_json(self) -> JSON: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Viewer:
    """The signed-in account, as the page header shows it."""

    name: str
    link_karma: int
    inbox_count: int
    # The CSRF token every form posts back as ``uh``.
    modhash: str
    # Moderates somewhere with mail access, so the header shows "mod messages".
    is_moderator: bool = False
    has_mod_mail: bool = False
    # Community stylesheets apply, as the viewer's preference says.
    show_stylesheets: bool = True
    # A site admin, and whether admin mode is on.
    may_admin: bool = False
    admin_mode: bool = False

    @property
    def has_mail(self) -> bool:
        return self.inbox_count > 0


@dataclass(frozen=True, kw_only=True)
class Page(View):
    """A full site page, with the header and the viewer's account."""

    has_json: ClassVar[bool] = False

    viewer: Viewer | None

    @property
    def title(self) -> str:
        return 'ina'

    def to_json(self) -> JSON:
        return None
