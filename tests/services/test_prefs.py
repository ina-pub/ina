from __future__ import annotations

import pytest

from app.errors import ErrorCode, InaError
from app.repos.base import Prefs
from app.services.prefs import from_form


def test_unticked_checkboxes_turn_off() -> None:
    prefs = from_form(Prefs(), {'newwindow': 'on'})
    assert prefs.newwindow is True
    assert prefs.no_profanity is False
    assert prefs.show_stylesheets is False


def test_numbers_and_choices() -> None:
    form = {
        'numsites': '100',
        'num_comments': '500',
        'min_link_score': '',
        'min_comment_score': '-10',
        'default_comment_sort': 'new',
        'media': 'off',
    }
    prefs = from_form(Prefs(), form)
    assert prefs.numsites == 100
    assert prefs.num_comments == 500
    assert prefs.min_link_score is None
    assert prefs.min_comment_score == -10
    assert prefs.default_comment_sort == 'new'
    assert prefs.media == 'off'
    assert prefs.media_preview == 'community'


def test_fields_off_the_form_are_kept() -> None:
    current = Prefs(content_langs=('en', 'de'))
    assert from_form(current, {}).content_langs == ('en', 'de')


@pytest.mark.parametrize(
    ('form', 'code', 'field'),
    [
        ({'numsites': '30'}, ErrorCode.INVALID_OPTION, 'numsites'),
        ({'num_comments': '501'}, ErrorCode.BAD_NUMBER, 'num_comments'),
        ({'min_link_score': 'lots'}, ErrorCode.BAD_NUMBER, 'min_link_score'),
        ({'media': 'sometimes'}, ErrorCode.INVALID_OPTION, 'media'),
        ({'default_comment_sort': 'random'}, ErrorCode.INVALID_OPTION, 'default_comment_sort'),
    ],
)
def test_invalid_values(form: dict[str, str], code: ErrorCode, field: str) -> None:
    with pytest.raises(InaError) as caught:
        from_form(Prefs(), form)
    (error,) = caught.value.errors
    assert (error.code, error.field) == (code, field)
