"""Community stylesheets: what a community may put in its own CSS, checked before it is served.

The source is parsed with tinycss2 and every at-rule, selector, property, function and URL is
checked against allowlists drawn from the CSS specifications. Anything not on them is refused, and
nothing is served while anything is; what is served is written out again from the parse, never
copied from the source text.

Three rules keep a community inside its own pages:

- A stylesheet loads nothing from elsewhere: ``url()`` may only name one of the community's
  uploaded images, written ``url(%%name%%)``.
- Escapes and control characters are refused in the source text before it is parsed at all,
  since browsers have disagreed about what they mean.
- Selectors may not reach outside the page (``html``, ``head``, ``:root`` and their like), and
  ``body`` may not be fixed in place, where it would cover the site's own header.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

import tinycss2
from tinycss2.ast import (
    AtRule,
    Declaration,
    FunctionBlock,
    IdentToken,
    LiteralToken,
    ParenthesesBlock,
    ParseError,
    QualifiedRule,
    SquareBracketsBlock,
    URLToken,
    WhitespaceToken,
)

from app.text.css import Serializer

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from tinycss2.ast import Node

MAX_SIZE_KIB = 100
IMAGE_REFERENCE = re.compile(r'\A%%([a-zA-Z0-9\-]+)%%\Z')
BROWSER_PREFIX = re.compile(r'\A-(?:webkit|moz|ms|o)-')


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


# Properties, by the specification that defines them. Nothing here can load a resource except
# through url(), which is checked on its own.
PROPERTIES = _words(
    # CSS Box Model, Box Sizing, Display, Positioned Layout, Overflow, Visual Effects
    """
    margin margin-top margin-right margin-bottom margin-left
    padding padding-top padding-right padding-bottom padding-left
    width height min-width min-height max-width max-height box-sizing aspect-ratio
    display visibility float clear position top right bottom left inset z-index
    overflow overflow-x overflow-y overflow-wrap clip clip-path opacity
    """
    # CSS Backgrounds and Borders
    """
    background background-color background-image background-repeat background-position
    background-position-x background-position-y background-size background-attachment
    background-origin background-clip background-blend-mode
    border border-width border-style border-color border-radius
    border-top border-right border-bottom border-left
    border-top-width border-right-width border-bottom-width border-left-width
    border-top-style border-right-style border-bottom-style border-left-style
    border-top-color border-right-color border-bottom-color border-left-color
    border-top-left-radius border-top-right-radius border-bottom-right-radius
    border-bottom-left-radius border-image border-image-source border-image-slice
    border-image-width border-image-outset border-image-repeat box-shadow
    outline outline-width outline-style outline-color outline-offset
    """
    # CSS Fonts, Text, Text Decoration, Writing Modes, Color
    """
    color font font-family font-size font-style font-weight font-variant font-stretch
    line-height letter-spacing word-spacing text-align text-align-last text-indent
    text-transform text-shadow text-overflow white-space word-break hyphens tab-size
    text-decoration text-decoration-line text-decoration-color text-decoration-style
    text-decoration-thickness text-underline-offset direction unicode-bidi writing-mode
    vertical-align
    """
    # CSS Flexible Box, Grid, Box Alignment
    """
    flex flex-basis flex-direction flex-flow flex-grow flex-shrink flex-wrap order
    grid grid-area grid-template grid-template-areas grid-template-columns grid-template-rows
    grid-auto-columns grid-auto-rows grid-auto-flow grid-column grid-column-start
    grid-column-end grid-row grid-row-start grid-row-end gap row-gap column-gap
    align-content align-items align-self justify-content justify-items justify-self
    place-content place-items place-self
    """
    # CSS Transforms, Transitions, Animations, Filter Effects, Compositing, Masking
    """
    transform transform-origin transform-style perspective perspective-origin
    backface-visibility transition transition-property transition-duration
    transition-timing-function transition-delay animation animation-name animation-duration
    animation-timing-function animation-delay animation-iteration-count animation-direction
    animation-fill-mode animation-play-state filter mix-blend-mode isolation
    mask mask-image mask-mode mask-repeat mask-position mask-clip mask-origin mask-size
    """
    # CSS Lists and Counters, Tables, Generated Content, Images, Basic User Interface
    """
    list-style list-style-type list-style-position list-style-image counter-reset
    counter-increment border-collapse border-spacing caption-side empty-cells table-layout
    content quotes object-fit object-position image-rendering cursor pointer-events resize
    appearance user-select caret-color accent-color will-change
    """
)
FUNCTIONS = _words(
    # CSS Values: calc and its relatives; CSS Color
    """
    calc min max clamp rgb rgba hsl hsla hwb
    """
    # CSS Images: gradients
    """
    linear-gradient radial-gradient conic-gradient repeating-linear-gradient
    repeating-radial-gradient repeating-conic-gradient
    """
    # CSS Transforms
    """
    matrix matrix3d translate translate3d translatex translatey translatez scale scale3d
    scalex scaley scalez rotate rotate3d rotatex rotatey rotatez skew skewx skewy perspective
    """
    # CSS Easing; CSS Shapes and Masking; Filter Effects; CSS Grid
    """
    cubic-bezier steps inset circle ellipse polygon blur brightness contrast drop-shadow
    grayscale hue-rotate invert saturate sepia repeat minmax fit-content
    """
    # Generated content; selector pseudo-classes
    """
    attr counter counters not is where has lang nth-child nth-last-child nth-of-type
    nth-last-of-type
    """
)
AT_RULES = _words('media supports keyframes')
# Element names for the document around the page, not the page.
OUTSIDE_THE_PAGE = _words('html head title meta link script style base iframe object embed')
# The kinds of token a value or selector may hold, besides blocks and functions.
PLAIN_TOKENS = _words('ident hash number percentage dimension string literal whitespace')

MESSAGES = {
    'TOO_BIG': 'stylesheets are limited to {size} KiB',
    'UNSAFE_CHARACTER': 'backslashes and control characters are not allowed',
    'SYNTAX_ERROR': "this couldn't be read: {message}",
    'STRAY_SEMICOLON': 'a semicolon is out of place here',
    'AT_RULE': '@{keyword} is not allowed; @media, @supports and @keyframes are',
    'PROPERTY': '"{name}" is not a property stylesheets may set',
    'FUNCTION': '"{name}()" is not a function stylesheets may use',
    'TOKEN': 'a {kind} is not allowed here',
    'EXTERNAL_URL': 'url() may only name an uploaded image, as url(%%name%%)',
    'NO_SUCH_IMAGE': 'there is no uploaded image called "{name}"',
    'OUTSIDE_THE_PAGE': '"{selector}" is outside the page; only the page can be styled',
    'FIXED_BODY': 'body may not be position: fixed',
}


@dataclass(frozen=True, slots=True)
class CssError:
    line: int
    code: str
    params: tuple[tuple[str, str | int], ...] = ()

    @property
    def message(self) -> str:
        return MESSAGES[self.code].format(**dict(self.params))

    def __str__(self) -> str:
        return f'line {self.line}: {self.message}' if self.line else self.message


def _at(where: Node | int, code: str, **params: str | int) -> CssError:
    line = where if isinstance(where, int) else where.source_line
    return CssError(line, code, tuple(params.items()))


def _base_name(name: str) -> str:
    """A property, function or at-rule's name without a browser's prefix."""
    return BROWSER_PREFIX.sub('', name.lower())


def _is_literal(node: Node | None, *values: str) -> bool:
    return isinstance(node, LiteralToken) and node.value in values


def _split_selectors(prelude: Sequence[Node]) -> list[list[Node]]:
    """A selector list's selectors, each without surrounding whitespace."""
    selectors: list[list[Node]] = [[]]
    for node in prelude:
        if _is_literal(node, ','):
            selectors.append([])
        else:
            selectors[-1].append(node)
    trimmed = []
    for selector in selectors:
        start, end = 0, len(selector)
        while start < end and isinstance(selector[start], WhitespaceToken):
            start += 1
        while end > start and isinstance(selector[end - 1], WhitespaceToken):
            end -= 1
        trimmed.append(selector[start:end])
    return trimmed


def _subject(selector: Sequence[Node]) -> list[Node]:
    """The last compound selector: what the selector picks out."""
    compound: list[Node] = []
    for node in selector:
        if isinstance(node, WhitespaceToken) or _is_literal(node, '>', '+', '~'):
            compound = []
        else:
            compound.append(node)
    return compound


class Checker:
    """Walks a parsed stylesheet, yielding a CssError for everything it refuses.

    It also collects what each ``url(%%name%%)`` becomes, for the serializer.
    """

    def __init__(self, images: Mapping[str, str]) -> None:
        self.images = images
        self.urls: dict[str, str] = {}

    def rules(self, nodes: Iterable[Node]) -> Iterator[CssError]:
        for node in nodes:
            if isinstance(node, QualifiedRule):
                yield from self.style_rule(node)
            elif isinstance(node, AtRule):
                yield from self.at_rule(node)
            elif isinstance(node, ParseError):
                yield _at(node, 'SYNTAX_ERROR', message=node.message)
            elif not isinstance(node, WhitespaceToken) and node.type != 'comment':
                yield _at(node, 'TOKEN', kind=node.type)

    def at_rule(self, rule: AtRule) -> Iterator[CssError]:
        if _base_name(rule.at_keyword) not in AT_RULES:
            yield _at(rule, 'AT_RULE', keyword=rule.at_keyword)
            return
        yield from self.tokens(rule.prelude)
        if rule.content is not None:
            yield from self.rules(tinycss2.parse_rule_list(rule.content))

    def style_rule(self, rule: QualifiedRule) -> Iterator[CssError]:
        yield from self.tokens(rule.prelude)
        selectors = _split_selectors(rule.prelude)
        for selector in selectors:
            yield from self.selector(selector)
        pins_body = any(self._is_body(_subject(selector)) for selector in selectors)
        for item in tinycss2.parse_declaration_list(rule.content):
            if isinstance(item, Declaration):
                yield from self.declaration(item, pins_body=pins_body)
            elif isinstance(item, ParseError):
                yield _at(item, 'SYNTAX_ERROR', message=item.message)
            elif isinstance(item, AtRule):
                yield _at(item, 'AT_RULE', keyword=item.at_keyword)

    def selector(self, selector: Sequence[Node]) -> Iterator[CssError]:
        for index, node in enumerate(selector):
            before = selector[index - 1] if index else None
            if not isinstance(node, IdentToken):
                continue
            # After "." or ":" a name is a class or pseudo-class; otherwise it is an element.
            if _is_literal(before, ':') and node.lower_value == 'root':
                yield _at(node, 'OUTSIDE_THE_PAGE', selector=':root')
            elif not _is_literal(before, '.', ':') and node.lower_value in OUTSIDE_THE_PAGE:
                yield _at(node, 'OUTSIDE_THE_PAGE', selector=node.value)

    @staticmethod
    def _is_body(compound: Sequence[Node]) -> bool:
        first = compound[0] if compound else None
        return isinstance(first, IdentToken) and first.lower_value == 'body'

    def declaration(self, item: Declaration, *, pins_body: bool) -> Iterator[CssError]:
        if _base_name(item.name) not in PROPERTIES:
            yield _at(item, 'PROPERTY', name=item.name)
            return
        fixed = any(isinstance(v, IdentToken) and v.lower_value == 'fixed' for v in item.value)
        if pins_body and item.lower_name == 'position' and fixed:
            yield _at(item, 'FIXED_BODY')
        yield from self.tokens(item.value)

    def tokens(self, nodes: Sequence[Node]) -> Iterator[CssError]:
        for node in nodes:
            if isinstance(node, URLToken):
                yield from self.url(node)
            elif isinstance(node, FunctionBlock):
                yield from self.function(node)
            elif isinstance(node, (ParenthesesBlock, SquareBracketsBlock)):
                yield from self.tokens(node.content)
            elif isinstance(node, ParseError):
                yield _at(node, 'SYNTAX_ERROR', message=node.message)
            elif _is_literal(node, ';'):
                # Inside a value, a semicolon could end it early in a lax parser.
                yield _at(node, 'STRAY_SEMICOLON')
            elif node.type not in PLAIN_TOKENS:
                # Curly blocks among them: no value or selector holds one.
                yield _at(node, 'TOKEN', kind=node.type)

    def function(self, node: FunctionBlock) -> Iterator[CssError]:
        if _base_name(node.name) not in FUNCTIONS:
            yield _at(node, 'FUNCTION', name=node.name)
            return
        if _base_name(node.name) == 'attr' and any(
            isinstance(arg, IdentToken) and arg.lower_value == 'url' for arg in node.arguments
        ):
            # attr(name url) would load whatever the attribute names.
            yield _at(node, 'EXTERNAL_URL')
        yield from self.tokens(node.arguments)

    def url(self, node: URLToken) -> Iterator[CssError]:
        reference = IMAGE_REFERENCE.match(node.value)
        if reference is None:
            yield _at(node, 'EXTERNAL_URL')
        elif reference[1] not in self.images:
            yield _at(node, 'NO_SUCH_IMAGE', name=reference[1])
        else:
            self.urls[node.value] = self.images[reference[1]]


def _unsafe(character: str) -> bool:
    """A backslash, or an invisible character: Unicode's control, format and unassigned ones."""
    if character == '\\':
        return True
    return character not in '\t\r\n' and unicodedata.category(character).startswith('C')


def _unsafe_characters(source: str) -> Iterator[CssError]:
    for number, line in enumerate(source.splitlines(), start=1):
        if any(_unsafe(character) for character in line):
            yield _at(number, 'UNSAFE_CHARACTER')


def sanitize(source: str, images: Mapping[str, str]) -> tuple[str, list[CssError]]:
    """The stylesheet as it would be served, and every error; it is served only without errors.

    ``images`` maps each uploaded image's name to its URL.
    """
    if len(source.encode()) > MAX_SIZE_KIB * 1024:
        return '', [_at(0, 'TOO_BIG', size=MAX_SIZE_KIB)]
    checker = Checker(images)
    found = [*_unsafe_characters(source), *checker.rules(tinycss2.parse_stylesheet(source))]
    if found:
        return '', sorted(found, key=lambda error: error.line)
    parsed = tinycss2.parse_stylesheet(source, skip_comments=True, skip_whitespace=True)
    return Serializer(checker.urls).rules(parsed), []
