"""Flair pages: the moderators' flair page, and the flair API's JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from app.views.base import View
from app.views.communities import CommunityPage

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import FlairTemplate, UserFlair
    from app.services.flair import CsvLine, Shown

POSITIONS = (('left', 'before the name'), ('right', 'after the name'))
LINK_POSITIONS = (('', 'hidden'), ('left', 'before the title'), ('right', 'after the title'))


def template_json(template: FlairTemplate, position: str) -> JSON:
    return {
        'flair_template_id': str(template.id),
        'flair_text': template.text,
        'flair_css_class': template.css_class,
        'flair_text_editable': template.text_editable,
        'flair_position': position,
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class FlairBox:
    """The sidebar's "show my flair on this community" box, and the choices to pick from."""

    name: str
    enabled: bool
    current: Shown | None
    choices: tuple[FlairTemplate, ...] = ()
    # Templates' text may be changed, by their setting or because the viewer moderates.
    editable: bool = False


@dataclass(frozen=True, kw_only=True)
class FlairPage(CommunityPage):
    """``/in/{name}/about/flair``: r2's flair pane, flair list and template editors, as forms."""

    template: ClassVar[str] = 'flair.html'
    cacheable: ClassVar[bool] = False

    users: tuple[UserFlair, ...] = ()
    # The name the list continues after, when it does.
    after: str | None = None
    user_templates: tuple[FlairTemplate, ...] = ()
    link_templates: tuple[FlairTemplate, ...] = ()
    errors: tuple[FieldError, ...] = ()

    positions: ClassVar[tuple[tuple[str, str], ...]] = POSITIONS
    link_positions: ClassVar[tuple[tuple[str, str], ...]] = LINK_POSITIONS

    @property
    def title(self) -> str:
        return f'flair of {self.community.name}'

    def to_json(self) -> JSON:
        position = self.community.settings.flair_position
        return {
            'users': [
                {'user': f.name, 'flair_text': f.text, 'flair_css_class': f.css_class}
                for f in self.users
            ],
            'user_templates': [template_json(t, position) for t in self.user_templates],
            'link_templates': [
                template_json(t, self.community.settings.link_flair_position)
                for t in self.link_templates
            ],
        }


@dataclass(frozen=True, kw_only=True)
class FlairList(View):
    """``/in/{name}/api/flairlist``, as r2's FlairListJsonTemplate."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    users: tuple[UserFlair, ...]
    next: str | None
    prev: str | None = None

    def to_json(self) -> JSON:
        users: list[JSON] = [
            {'user': f.name, 'flair_text': f.text, 'flair_css_class': f.css_class}
            for f in self.users
        ]
        result: dict[str, JSON] = {'users': users}
        if self.next:
            result['next'] = self.next
        if self.prev:
            result['prev'] = self.prev
        return result


@dataclass(frozen=True, kw_only=True)
class FlairSelector(View):
    """``/in/{name}/api/flairselector``: the current flair and the templates to choose from."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    current: Shown | None
    position: str
    choices: tuple[FlairTemplate, ...] = field(default=())

    def to_json(self) -> JSON:
        current = self.current
        return {
            'current': {
                'flair_text': current.text if current else None,
                'flair_css_class': current.css_class if current else None,
                'flair_position': self.position,
                'flair_template_id': None,
            },
            'choices': [template_json(t, self.position) for t in self.choices],
        }


@dataclass(frozen=True, kw_only=True)
class CsvResults(View):
    """``/in/{name}/api/flaircsv``: one result per row."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    lines: tuple[CsvLine, ...]

    def to_json(self) -> JSON:
        results: list[JSON] = []
        for line in self.lines:
            warnings: dict[str, JSON] = dict(line.warnings)
            errors: dict[str, JSON] = dict(line.errors)
            results.append(
                {'ok': line.ok, 'status': line.status, 'warnings': warnings, 'errors': errors}
            )
        return results
