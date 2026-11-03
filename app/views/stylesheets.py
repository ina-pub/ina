"""The community stylesheet editor, as r2's /about/stylesheet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.views.communities import CommunityPage

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import CommunityImage


@dataclass(frozen=True, slots=True)
class ImageRow:
    image: CommunityImage
    url: str


@dataclass(frozen=True, kw_only=True)
class StylesheetPage(CommunityPage):
    """``/in/{name}/about/stylesheet``: the source, its errors, and the images it may use."""

    template: ClassVar[str] = 'stylesheet.html'
    cacheable: ClassVar[bool] = False

    source: str
    images: tuple[ImageRow, ...] = ()
    header_url: str | None = None
    errors: tuple[FieldError, ...] = ()
    saved: bool = False

    @property
    def title(self) -> str:
        return f'stylesheet of {self.community.name}'

    def to_json(self) -> JSON:
        """r2's ``stylesheet`` object."""
        return {
            'kind': 'stylesheet',
            'data': {
                'stylesheet': self.source,
                'community_id': str(self.community.fullname),
                'images': [
                    {'name': row.image.name, 'url': row.url, 'link': f'url(%%{row.image.name}%%)'}
                    for row in self.images
                ],
            },
        }
