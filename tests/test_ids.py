from __future__ import annotations

import pytest

from app.ids import Fullname, Kind, from36, to36


@pytest.mark.parametrize(('number', 'text'), [(0, '0'), (35, 'z'), (36, '10'), (2662, '21y')])
def test_base36_round_trips(number: int, text: str) -> None:
    assert to36(number) == text
    assert from36(text) == number


@pytest.mark.parametrize('text', ['', 'ABC', '-1', 'a_b', ' 1'])
def test_from36_rejects_anything_else(text: str) -> None:
    with pytest.raises(ValueError, match='base36'):
        from36(text)


def test_fullname() -> None:
    fullname = Fullname(Kind.ACCOUNT, 2662)
    assert str(fullname) == 't2_21y'
    assert Fullname.parse('t2_21y') == fullname
    assert fullname.id36 == '21y'


@pytest.mark.parametrize('text', ['t2', 't9_1', 'x2_1', 't2_', 't2_A'])
def test_fullname_parse_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError, match='fullname'):
        Fullname.parse(text)
