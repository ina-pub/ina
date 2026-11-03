"""reddit markdown cases the renderer must keep producing."""

from __future__ import annotations

import pytest

from app.text import html, markdown

CASES = [
    ('**bold** *em* ~~del~~', '<p><strong>bold</strong> <em>em</em> <del>del</del></p>\n'),
    ('2^10 and ^(two words)', '<p>2<sup>10</sup> and <sup>two words</sup></p>\n'),
    ('>!hidden!<', '<p><span class="md-spoiler-text">hidden</span></p>\n'),
    (
        'see /in/pics and /u/spez',
        (
            '<p>see <a href="/in/pics" rel="nofollow">/in/pics</a> and '
            '<a href="/u/spez" rel="nofollow">/u/spez</a></p>\n'
        ),
    ),
    # The vendored snudown links in/name; r/name, and in/ after a hyphen, stay text.
    (
        'in/pics, not r/pics or check-in/out',
        '<p><a href="/in/pics" rel="nofollow">in/pics</a>, not r/pics or check-in/out</p>\n',
    ),
    (
        '> one\n>> two',
        '<blockquote>\n<p>one</p>\n\n<blockquote>\n<p>two</p>\n</blockquote>\n</blockquote>\n',
    ),
    (
        '| a |\n|:-:|\n| b |',
        (
            '<table><thead>\n<tr>\n<th align="center">a</th>\n</tr>\n</thead><tbody>\n<tr>\n'
            '<td align="center">b</td>\n</tr>\n</tbody></table>\n'
        ),
    ),
    ('', ''),
    ('   \n', ''),
]


@pytest.mark.parametrize(('text', 'expected'), CASES)
def test_render(text: str, expected: str) -> None:
    assert markdown.render(text) == expected


@pytest.mark.parametrize(
    'text',
    ['<script>alert(1)</script>', '[x](javascript:alert(1))', '<img src=x onerror=alert(1)>'],
)
def test_render_never_emits_active_markup(text: str) -> None:
    rendered = markdown.render(text)
    assert '<script' not in rendered
    assert 'href="javascript' not in rendered
    assert '<img' not in rendered


@pytest.mark.parametrize(
    ('dirty', 'cleaned'),
    [
        ('<p onclick="x()">hi</p>', '<p>hi</p>'),
        ('<a href="javascript:alert(1)">x</a>', '<a rel="nofollow">x</a>'),
        ('<span class="evil md-spoiler-text">s</span>', '<span class="md-spoiler-text">s</span>'),
        ('<iframe src="https://x"></iframe><b>b</b>', 'b'),
        ('<a href="/in/pics">r</a>', '<a href="/in/pics" rel="nofollow">r</a>'),
    ],
)
def test_allowlist_strips_what_snudown_never_makes(dirty: str, cleaned: str) -> None:
    assert html.clean(dirty) == cleaned


def test_wiki_pages_name_their_headers_as_r2_did() -> None:
    html = markdown.render_wiki('# Intro\n\n## Rules\n\n## Rules\n\n### Ünïcode & more!\n\nbody')
    assert '<a href="#wiki_intro" rel="nofollow">Intro</a>' in html
    assert '<h2 id="wiki_rules">Rules</h2>' in html
    assert '<h2 id="wiki_rules2">Rules</h2>' in html
    assert '<h3 id="wiki_.FCn.EFcode_.26_more.21">' in html
    assert html.startswith('<div class="toc">')


def test_wiki_text_cannot_add_ids_or_markup() -> None:
    # Snudown escapes raw HTML, so only the ids it writes itself survive.
    raw = '<div id="wiki_x" class="toc">x</div> <h1 id="nope">y</h1>'
    assert markdown.render_wiki(raw) == (
        '<p>&lt;div id="wiki_x" class="toc"&gt;x&lt;/div&gt; &lt;h1 id="nope"&gt;y&lt;/h1&gt;</p>\n'
    )
    # And the allowlist drops ids that are not the wiki's own, whatever made them.
    assert html.clean_wiki('<h1 id="nope">y</h1><h2 id="wiki_ok">z</h2>') == (
        '<h1>y</h1><h2 id="wiki_ok">z</h2>'
    )
    assert markdown.render_wiki('   ') == ''
