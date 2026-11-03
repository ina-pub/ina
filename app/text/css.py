"""CSS that the site serves: re-serialized from tinycss2's parse, never edited as text.

``minify`` drops comments and runs of whitespace. Community stylesheets use the same serializer,
with their ``url(%%name%%)`` placeholders swapped for the uploaded images' URLs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tinycss2
from tinycss2.ast import (
    AtRule,
    Comment,
    CurlyBracketsBlock,
    Declaration,
    FunctionBlock,
    LiteralToken,
    ParenthesesBlock,
    QualifiedRule,
    SquareBracketsBlock,
    URLToken,
    WhitespaceToken,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from tinycss2.ast import Node

# At-rules whose block holds rules; the others hold declarations.
RULE_BLOCKS = {'media', 'supports', 'keyframes', '-webkit-keyframes', 'document', 'layer'}
_NO_SPACE_AFTER = (',', '(')


class Serializer:
    def __init__(self, urls: Mapping[str, str] | None = None) -> None:
        # url() values to replace, such as {'%%header%%': '/media/community/1/0a1b.png'}.
        self.urls = urls or {}

    def values(self, tokens: Sequence[Node]) -> str:
        """Component values, with comments gone and any whitespace as one space."""
        pieces: list[str] = []
        for token in tokens:
            if isinstance(token, Comment):
                continue
            if isinstance(token, WhitespaceToken):
                if pieces and not pieces[-1].endswith(_NO_SPACE_AFTER) and pieces[-1] != ' ':
                    pieces.append(' ')
                continue
            if (
                isinstance(token, LiteralToken)
                and token.value in {',', ')'}
                and pieces[-1:] == [' ']
            ):
                pieces.pop()
            pieces.append(self.value(token))
        return ''.join(pieces).strip()

    def value(self, token: Node) -> str:
        if isinstance(token, URLToken) and token.value in self.urls:
            return f'url({self.urls[token.value]})'
        if isinstance(token, FunctionBlock):
            return f'{token.name}({self.values(token.arguments)})'
        if isinstance(token, ParenthesesBlock):
            return f'({self.values(token.content)})'
        if isinstance(token, SquareBracketsBlock):
            return f'[{self.values(token.content)}]'
        if isinstance(token, CurlyBracketsBlock):
            return f'{{{self.values(token.content)}}}'
        return token.serialize()

    def declaration(self, item: Declaration) -> str:
        important = '!important' if item.important else ''
        return f'{item.name}:{self.values(item.value)}{important}'

    def declarations(self, content: Sequence[Node]) -> str:
        items = tinycss2.parse_declaration_list(content, skip_comments=True, skip_whitespace=True)
        return ';'.join(self.declaration(item) for item in items if isinstance(item, Declaration))

    def rules(self, nodes: Sequence[Node]) -> str:
        out = []
        for node in nodes:
            if isinstance(node, QualifiedRule):
                out.append(f'{self.values(node.prelude)}{{{self.declarations(node.content)}}}')
            elif isinstance(node, AtRule):
                prelude = self.values(node.prelude)
                head = f'@{node.at_keyword}{" " + prelude if prelude else ""}'
                if node.content is None:
                    out.append(f'{head};')
                elif node.lower_at_keyword in RULE_BLOCKS:
                    inner = tinycss2.parse_rule_list(
                        node.content, skip_comments=True, skip_whitespace=True
                    )
                    out.append(f'{head}{{{self.rules(inner)}}}')
                else:
                    out.append(f'{head}{{{self.declarations(node.content)}}}')
        return ''.join(out)


def minify(css: str) -> str:
    return Serializer().rules(
        tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    )
