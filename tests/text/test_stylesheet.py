from __future__ import annotations

import pytest

from app.text.stylesheet import MAX_SIZE_KIB, sanitize

IMAGES = {'banner': '/media/community/1/0a1b2c.png'}


def errors(css: str) -> list[str]:
    return [str(error) for error in sanitize(css, IMAGES)[1]]


def test_a_good_stylesheet_is_served_minified_with_images() -> None:
    css = """
    /* our header */
    #header { background: #369 url(%%banner%%) no-repeat; }
    .link .title:hover , .side h1 { color : rgba(0, 0, 0, .5) !important }
    @media (max-width: 600px) { .side { display: none } }
    @supports (display: grid) { .side { display: grid } }
    body { position: relative }
    body .side { position: fixed }
    """
    served, problems = sanitize(css, IMAGES)
    assert problems == []
    assert served == (
        '#header{background:#369 url(/media/community/1/0a1b2c.png) no-repeat}'
        '.link .title:hover,.side h1{color:rgba(0,0,0,.5)!important}'
        '@media (max-width: 600px){.side{display:none}}'
        '@supports (display: grid){.side{display:grid}}'
        'body{position:relative}'
        'body .side{position:fixed}'
    )


@pytest.mark.parametrize(
    ('css', 'error'),
    [
        ('a { background: url(http://e.example/x.png) }', 'url() may only name an uploaded image'),
        ('a { background: url(%%missing%%) }', 'there is no uploaded image called "missing"'),
        ('a { background: url("%%banner%%") }', '"url()" is not a function'),
        ('a { color: expression(alert(1)) }', '"expression()" is not a function'),
        ('a { behavior: url(x.htc) }', '"behavior" is not a property'),
        ('a { -moz-binding: url(x) }', '"-moz-binding" is not a property'),
        ('a { content: attr(href url) }', 'url() may only name an uploaded image'),
        ('a { color: expr\\65ssion(1) }', 'backslashes and control characters are not allowed'),
        ("a { font-family: 'x\x03' }", 'backslashes and control characters are not allowed'),
        ('@import url(x.css);', '@import is not allowed'),
        ('@font-face { font-family: x; src: url(%%banner%%) }', '@font-face is not allowed'),
        ('p[foo=bar{}*{background:green}] { color: red }', 'a {} block is not allowed here'),
        ('a { color: calc(;color:red;) }', 'a semicolon is out of place here'),
        ('body { position: fixed }', 'body may not be position: fixed'),
        ('a, body.night { position: fixed }', 'body may not be position: fixed'),
        ('html { background: red }', '"html" is outside the page'),
        (':root { color: red }', '":root" is outside the page'),
        ('head script { display: block }', '"head" is outside the page'),
        ('a { color: red\u202e }', 'backslashes and control characters are not allowed'),
        ('@page { margin: 0 }', '@page is not allowed'),
    ],
)
def test_what_is_refused(css: str, error: str) -> None:
    found = errors(css)
    assert any(error in message for message in found), found


def test_errors_name_their_lines_and_nothing_is_served() -> None:
    served, problems = sanitize('a { color: red }\n\nb { colour: red }', IMAGES)
    assert served == ''
    assert [str(problem) for problem in problems] == [
        'line 3: "colour" is not a property stylesheets may set'
    ]


def test_size_limit() -> None:
    assert errors('a{}' * (MAX_SIZE_KIB * 400)) == [
        f'stylesheets are limited to {MAX_SIZE_KIB} KiB'
    ]
