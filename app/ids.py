"""Fullnames: a type prefix plus the base36 of a row's integer primary key (``t2_1w72``).

This is the only module that encodes or decodes base36.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from enum import StrEnum

DIGITS = string.digits + string.ascii_lowercase


class Kind(StrEnum):
    """The type prefixes, in the order r2 registers its thing types."""

    COMMENT = 't1'
    ACCOUNT = 't2'
    LINK = 't3'
    MESSAGE = 't4'
    COMMUNITY = 't5'
    AWARD = 't6'


def to36(number: int) -> str:
    if number < 0:
        msg = f'base36 ids are non-negative, got {number}'
        raise ValueError(msg)
    if number == 0:
        return '0'
    digits = []
    while number:
        number, remainder = divmod(number, 36)
        digits.append(DIGITS[remainder])
    return ''.join(reversed(digits))


def from36(text: str) -> int:
    """Decode base36; raise ``ValueError`` for anything that is not lowercase base36."""
    if not text or any(char not in DIGITS for char in text):
        msg = f'not a base36 id: {text!r}'
        raise ValueError(msg)
    return int(text, 36)


@dataclass(frozen=True, slots=True)
class Fullname:
    kind: Kind
    id: int

    def __str__(self) -> str:
        return f'{self.kind}_{to36(self.id)}'

    @property
    def id36(self) -> str:
        return to36(self.id)

    @classmethod
    def parse(cls, text: str) -> Fullname:
        prefix, _, id36 = text.partition('_')
        try:
            return cls(Kind(prefix), from36(id36))
        except ValueError:
            msg = f'not a fullname: {text!r}'
            raise ValueError(msg) from None
