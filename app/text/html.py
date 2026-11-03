"""The HTML allowlist every rendered user text passes through before it is stored.

It is independent of the markdown renderer on purpose: a renderer bug cannot ship markup that
is not on this list. The list is what snudown produces and nothing more.
"""

from __future__ import annotations

import nh3

TAGS = frozenset(
    {
        'a',
        'blockquote',
        'br',
        'code',
        'del',
        'em',
        'h1',
        'h2',
        'h3',
        'h4',
        'h5',
        'h6',
        'hr',
        'li',
        'ol',
        'p',
        'pre',
        'span',
        'strong',
        'sup',
        'table',
        'tbody',
        'td',
        'th',
        'thead',
        'tr',
        'ul',
    }
)
# Classes are governed by ALLOWED_CLASSES, so 'class' is not listed here.
ATTRIBUTES = {'a': {'href', 'title'}, 'th': {'align'}, 'td': {'align'}}
ALLOWED_CLASSES = {'span': {'md-spoiler-text'}}
URL_SCHEMES = frozenset({'http', 'https', 'ftp', 'mailto', 'irc', 'ircs', 'news', 'steam'})


# Wiki pages add their table of contents and the ids it links to, which r2 named wiki_*.
WIKI_TAGS = TAGS | {'div'}
HEADERS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
WIKI_ALLOWED_CLASSES = {**ALLOWED_CLASSES, 'div': {'toc'}}


def _wiki_ids(tag: str, attribute: str, value: str) -> str | None:
    if attribute == 'id':
        return value if tag in HEADERS and value.startswith('wiki_') else None
    return value


def clean(html: str) -> str:
    return nh3.clean(
        html,
        tags=set(TAGS),
        attributes={tag: set(names) for tag, names in ATTRIBUTES.items()},
        allowed_classes={tag: set(names) for tag, names in ALLOWED_CLASSES.items()},
        url_schemes=set(URL_SCHEMES),
        link_rel='nofollow',
    )


def clean_wiki(html: str) -> str:
    return nh3.clean(
        html,
        tags=set(WIKI_TAGS),
        attributes={
            **{tag: set(names) for tag, names in ATTRIBUTES.items()},
            **{header: {'id'} for header in HEADERS},
        },
        allowed_classes={tag: set(names) for tag, names in WIKI_ALLOWED_CLASSES.items()},
        url_schemes=set(URL_SCHEMES),
        link_rel='nofollow',
        attribute_filter=_wiki_ids,
    )
