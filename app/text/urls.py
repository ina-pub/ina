"""Submitted URLs and title slugs, checked and shaped the way r2 did."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

SCHEMES = frozenset({'http', 'https', 'ftp', 'mailto'})
DNS_LABEL = re.compile(r'\A[-a-zA-Z0-9_]+\Z')
MAX_HOSTNAME = 255
SLUG_LENGTH = 50
WHITESPACE = re.compile(r'\s+')
NOT_SAFE = re.compile(r'\W+')
UNDERSCORES = re.compile(r'_+')


def clean_url(url: str) -> str | None:
    """The URL to store, or None if it is not one. A URL without a scheme gets ``http://``."""
    url = url.strip()
    try:
        parts = urlsplit(url)
        if url and not parts.scheme:
            url = ('http:' if parts.hostname else 'http://') + url
            parts = urlsplit(url)
        ascii_host = (parts.hostname or '').encode('idna').decode().removesuffix('.')
    except (ValueError, UnicodeError):
        return None
    valid = (
        parts.scheme in SCHEMES
        and 0 < len(ascii_host) <= MAX_HOSTNAME
        and all(DNS_LABEL.match(label) for label in ascii_host.split('.'))
        and '%00' not in unquote(parts.path)
        and parts.username is None
        and parts.password is None
    )
    return url if valid else None


def domain(url: str) -> str:
    """The domain a link is listed under: its hostname without ``www.``."""
    hostname = urlsplit(url).hostname or ''
    return hostname.removeprefix('www.')


def slug(title: str) -> str:
    """r2's ``title_to_url``: the title as a URL segment, cut at a word under 50 characters."""
    text = WHITESPACE.sub('_', title)
    text = NOT_SAFE.sub('', text)
    text = UNDERSCORES.sub('_', text).strip('_').lower()
    if len(text) > SLUG_LENGTH:
        text = text[:SLUG_LENGTH]
        if (cut := text.rfind('_')) > 0:
            text = text[:cut]
    return text or '_'
