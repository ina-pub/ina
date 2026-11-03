# The parts of tinycss2.ast the app uses.

class Node:
    type: str
    source_line: int
    source_column: int
    def serialize(self) -> str: ...

class ParseError(Node):
    kind: str
    message: str

class Comment(Node):
    value: str

class WhitespaceToken(Node):
    value: str

class LiteralToken(Node):
    value: str

class IdentToken(Node):
    value: str
    lower_value: str

class AtKeywordToken(Node):
    value: str
    lower_value: str

class HashToken(Node):
    value: str
    is_identifier: bool

class StringToken(Node):
    value: str
    representation: str

class URLToken(Node):
    value: str
    representation: str

class UnicodeRangeToken(Node): ...

class NumberToken(Node):
    value: float
    int_value: int | None
    representation: str

class PercentageToken(NumberToken): ...

class DimensionToken(NumberToken):
    unit: str
    lower_unit: str

class ParenthesesBlock(Node):
    content: list[Node]

class SquareBracketsBlock(Node):
    content: list[Node]

class CurlyBracketsBlock(Node):
    content: list[Node]

class FunctionBlock(Node):
    name: str
    lower_name: str
    arguments: list[Node]

class Declaration(Node):
    name: str
    lower_name: str
    value: list[Node]
    important: bool

class QualifiedRule(Node):
    prelude: list[Node]
    content: list[Node]

class AtRule(Node):
    at_keyword: str
    lower_at_keyword: str
    prelude: list[Node]
    content: list[Node] | None
