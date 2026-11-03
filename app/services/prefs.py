"""Validating the preferences form into a new ``Prefs``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

from app.errors import ErrorCode, InaError
from app.repos.base import Prefs

if TYPE_CHECKING:
    from collections.abc import Mapping

NUMSITES = (10, 25, 50, 100)
NUM_COMMENTS = (1, 500)
SCORE_THRESHOLD = (-100, 100)
COMMENT_SORTS = ('confidence', 'top', 'new', 'controversial', 'old', 'qa')
MEDIA = ('on', 'off', 'community')
LANGS = ('en',)

# Fields the form does not edit; their current values are kept.
NOT_ON_FORM = frozenset({'content_langs'})


def _number(form: Mapping[str, str], name: str, low: int, high: int) -> int | None:
    text = form.get(name, '').strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        number = None
    if number is None or not low <= number <= high:
        raise InaError.of(ErrorCode.BAD_NUMBER, name, range=f'{low} - {high}')
    return number


def _choice(form: Mapping[str, str], name: str, choices: tuple[str, ...], current: str) -> str:
    value = form.get(name, current)
    if value not in choices:
        raise InaError.of(ErrorCode.INVALID_OPTION, name)
    return value


def from_form(current: Prefs, form: Mapping[str, str]) -> Prefs:
    """The prefs a submitted form asks for. An unticked checkbox is absent, so it means False."""
    values: dict[str, object] = {}
    for field in msgspec.structs.fields(Prefs):
        if field.name in NOT_ON_FORM:
            continue
        if field.type is bool:
            values[field.name] = form.get(field.name, 'off') not in {'off', ''}
    numsites = form.get('numsites', str(current.numsites))
    if numsites not in {str(choice) for choice in NUMSITES}:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'numsites')
    num_comments = _number(form, 'num_comments', *NUM_COMMENTS)
    values.update(
        numsites=int(numsites),
        num_comments=num_comments or current.num_comments,
        min_link_score=_number(form, 'min_link_score', *SCORE_THRESHOLD),
        min_comment_score=_number(form, 'min_comment_score', *SCORE_THRESHOLD),
        default_comment_sort=_choice(
            form, 'default_comment_sort', COMMENT_SORTS, current.default_comment_sort
        ),
        media=_choice(form, 'media', MEDIA, current.media),
        media_preview=_choice(form, 'media_preview', MEDIA, current.media_preview),
        lang=_choice(form, 'lang', LANGS, current.lang),
    )
    return msgspec.structs.replace(current, **values)
