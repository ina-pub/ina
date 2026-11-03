from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import msgspec

from app.services.prefs import COMMENT_SORTS, LANGS, MEDIA, NUMSITES
from app.views.base import Page, View

if TYPE_CHECKING:
    from app.errors import FieldError
    from app.jsontypes import JSON
    from app.repos.base import Prefs

COMMENT_SORT_LABELS = {
    'confidence': 'best',
    'top': 'top',
    'new': 'new',
    'controversial': 'controversial',
    'old': 'old',
    'qa': 'Q&A',
}
LANG_LABELS = {'en': 'English [en]'}


@dataclass(frozen=True, kw_only=True)
class PrefsPage(Page):
    """``/prefs``: the options form."""

    template: ClassVar[str] = 'prefs.html'

    prefs: Prefs
    done: bool = False
    error: str | None = None

    numsites_choices: ClassVar[tuple[int, ...]] = NUMSITES
    media_choices: ClassVar[tuple[str, ...]] = MEDIA
    comment_sorts: ClassVar[tuple[tuple[str, str], ...]] = tuple(
        (sort, COMMENT_SORT_LABELS[sort]) for sort in COMMENT_SORTS
    )
    langs: ClassVar[tuple[tuple[str, str], ...]] = tuple(
        (lang, LANG_LABELS[lang]) for lang in LANGS
    )

    @property
    def title(self) -> str:
        return 'preferences'


@dataclass(frozen=True, kw_only=True)
class PrefsUpdatePage(Page):
    """``/prefs/update``: change email or password."""

    template: ClassVar[str] = 'prefs_update.html'

    email: str | None
    email_verified: bool
    errors: tuple[FieldError, ...] = ()
    done: str | None = None

    @property
    def title(self) -> str:
        return 'update preferences'


@dataclass(frozen=True, kw_only=True)
class PrefsJson(View):
    """``/api/v1/me/prefs``."""

    template: ClassVar[str] = ''
    json_only: ClassVar[bool] = True
    cacheable: ClassVar[bool] = False

    prefs: Prefs

    def to_json(self) -> JSON:
        data: JSON = msgspec.to_builtins(self.prefs)
        return data
