"""Markdown for every user-written field, rendered once on write and stored as HTML."""

from __future__ import annotations

import re
from collections import Counter
from html import unescape

import snudown

from app.text import html


def render(text: str) -> str:
    """Snudown's HTML for ``text``, passed through the allowlist; empty text renders empty."""
    if not text.strip():
        return ''
    return html.clean(snudown.markdown(text, nofollow=True))


WIKI_TOC_ID = re.compile(r'<h([1-6]) id="wiki_toc_(\d+)">(.*?)</h\1>', re.DOTALL)
# r2 kept letters, digits, _ . and -, in ASCII, and wrote anything else as .HEX.
WIKI_ID_UNSAFE = re.compile(r'[^\w.-]', re.ASCII)
TAG = re.compile(r'<[^>]+>')


def _wiki_id(title: str) -> str:
    plain = unescape(TAG.sub('', title))
    aid = f'wiki_{plain.replace(" ", "_").lower()}'
    return WIKI_ID_UNSAFE.sub(lambda match: f'.{ord(match.group()):X}', aid)


def render_wiki(text: str) -> str:
    """A wiki page: snudown's wiki renderer with a table of contents, ids named as r2 named them.

    Snudown numbers its header ids; r2 named them after the header, ``wiki_rules`` for "Rules",
    so links into r2 wikis keep working. A repeated header gets 2, 3... after its name.
    """
    if not text.strip():
        return ''
    rendered = snudown.markdown(
        text, nofollow=True, renderer=snudown.RENDERER_WIKI, enable_toc=True, toc_id_prefix='wiki_'
    )
    seen: Counter[str] = Counter()
    names: dict[str, str] = {}
    for match in WIKI_TOC_ID.finditer(rendered):
        aid = _wiki_id(match[3])
        seen[aid] += 1
        names[match[2]] = aid if seen[aid] == 1 else f'{aid}{seen[aid]}'
    # Only snudown writes these attributes: user text that looks like them is escaped.
    for number, aid in names.items():
        rendered = rendered.replace(f'id="wiki_toc_{number}"', f'id="{aid}"', 1)
        rendered = rendered.replace(f'href="#wiki_toc_{number}"', f'href="#{aid}"', 1)
    return html.clean_wiki(rendered)
