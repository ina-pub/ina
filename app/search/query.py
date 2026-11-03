"""r2's search syntax, turned into an OpenSearch query.

Bare words and "quoted phrases" search titles and self text. ``field:value`` and
``field:"a phrase"`` search one field: in (a community), author, self, nsfw, flair, url, site,
title and selftext. Terms are joined with AND unless OR is written; NOT or a leading ``-``
excludes a term, and parentheses group. AND, OR and NOT are only operators in capitals, as in
Lucene.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infra.opensearch import Body

TEXT_FIELDS = ['title^2', 'selftext']
FIELDS = {'in', 'author', 'self', 'nsfw', 'flair', 'url', 'site', 'title', 'selftext'}
YES, NO = {'yes', 'true', '1'}, {'no', 'false', '0'}
TOKEN = re.compile(
    r'"(?P<phrase>[^"]*)"|(?P<open>\()|(?P<close>\))|(?P<word>[^\s()"]+)|(?P<quote>")'
)
PUNCTUATION = re.compile(r'[^\w\s]+')


class QueryError(ValueError):
    """The query cannot be read: unbalanced quotes or parentheses, or a dangling operator."""


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    text: str


def _tokens(text: str) -> list[Token]:
    tokens = []
    for found in TOKEN.finditer(text):
        kind = found.lastgroup or ''
        if kind == 'quote':
            msg = 'a quote is not closed'
            raise QueryError(msg)
        tokens.append(Token(kind, found[kind]))
    return tokens


def _flag(name: str, value: str) -> bool:
    if value.lower() in YES:
        return True
    if value.lower() in NO:
        return False
    msg = f'{name}: expected yes or no'
    raise QueryError(msg)


def _field(name: str, value: str, *, phrase: bool) -> Body:
    if not value.strip():
        msg = f'{name}: nothing to search for'
        raise QueryError(msg)
    if name == 'in':
        return {'term': {'community': value.lower().removeprefix('/').removeprefix('in/')}}
    if name == 'author':
        return {'term': {'author': value.lower().removeprefix('/').removeprefix('u/')}}
    if name in {'self', 'nsfw'}:
        return {'term': {'is_self' if name == 'self' else 'nsfw': _flag(name, value)}}
    if name == 'site':
        return {'term': {'sites': value.lower().removeprefix('www.')}}
    if phrase or name == 'url':
        return {'match_phrase': {name: value}}
    return {'match': {name: {'query': value, 'operator': 'and'}}}


def _text(value: str, *, phrase: bool) -> Body:
    if phrase:
        return {'multi_match': {'query': value, 'fields': TEXT_FIELDS, 'type': 'phrase'}}
    return {'multi_match': {'query': value, 'fields': TEXT_FIELDS, 'operator': 'and'}}


class _Parser:
    """Recursive descent, lowest precedence first.

    or    := and (OR and)*
    and   := unary (AND? unary)*
    unary := NOT unary | -word | atom
    atom  := ( or ) | "phrase" | field:value | word
    """

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.at = 0

    def peek(self) -> Token | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            msg = 'the query ends too soon'
            raise QueryError(msg)
        self.at += 1
        return token

    def is_word(self, text: str) -> bool:
        token = self.peek()
        return token is not None and token.kind == 'word' and token.text == text

    def any_of(self) -> Body:
        clauses = [self.all_of()]
        while self.is_word('OR'):
            self.take()
            clauses.append(self.all_of())
        if len(clauses) == 1:
            return clauses[0]
        return {'bool': {'should': clauses, 'minimum_should_match': 1}}

    def all_of(self) -> Body:
        clauses = [self.unary()]
        while (
            (token := self.peek()) is not None and token.kind != 'close' and not self.is_word('OR')
        ):
            if self.is_word('AND'):
                self.take()
            clauses.append(self.unary())
        return clauses[0] if len(clauses) == 1 else {'bool': {'must': clauses}}

    def unary(self) -> Body:
        if self.is_word('NOT'):
            self.take()
            return {'bool': {'must_not': [self.unary()]}}
        token = self.peek()
        negated = token is not None and token.kind == 'word' and token.text.startswith('-')
        if token is not None and negated and token.text != '-':
            self.take()
            return {'bool': {'must_not': [self.word(token.text[1:])]}}
        return self.atom()

    def atom(self) -> Body:
        token = self.take()
        if token.kind == 'open':
            inner = self.any_of()
            closing = self.peek()
            if closing is None or closing.kind != 'close':
                msg = 'a parenthesis is not closed'
                raise QueryError(msg)
            self.take()
            return inner
        if token.kind == 'close':
            msg = 'a parenthesis closes nothing'
            raise QueryError(msg)
        if token.kind == 'phrase':
            if not token.text.strip():
                msg = 'an empty phrase'
                raise QueryError(msg)
            return _text(token.text, phrase=True)
        if token.text in {'AND', 'OR'}:
            msg = f'{token.text} needs a term on each side'
            raise QueryError(msg)
        return self.word(token.text)

    def word(self, text: str) -> Body:
        name, colon, value = text.partition(':')
        if not colon or name.lower() not in FIELDS:
            return _text(text, phrase=False)
        if value:
            return _field(name.lower(), value, phrase=False)
        # field:"a phrase" or field: value
        following = self.take()
        if following.kind not in {'word', 'phrase'}:
            msg = f'{name}: nothing to search for'
            raise QueryError(msg)
        return _field(name.lower(), following.text, phrase=following.kind == 'phrase')


def parse(text: str) -> Body | None:
    """The query clause, or None for a query with nothing in it."""
    tokens = _tokens(text)
    if not tokens:
        return None
    parser = _Parser(tokens)
    clause = parser.any_of()
    if parser.peek() is not None:
        msg = 'a parenthesis closes nothing'
        raise QueryError(msg)
    return clause


def fielded(text: str) -> bool:
    """Whether the query names a field; r2 showed no community results for those."""
    return ':' in text


def clean(text: str) -> str:
    """What r2 searched instead of a query it could not read: words only, in lower case."""
    return ' '.join(PUNCTUATION.sub(' ', text).lower().split())
