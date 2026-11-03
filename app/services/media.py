"""Thumbnails for links, made as r2 made them.

A link to an image is its own thumbnail. A page gives its Open Graph image, its ``image_src``
link, or failing those its largest roughly square image. The image is squared off by cutting
away the slices with the least detail, shrunk to 70 pixels square (140 for high-density screens
where it is big enough), and stored under a name made from its content.
"""

from __future__ import annotations

import hashlib
import io
import math
import warnings
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from PIL import Image, ImageOps, UnidentifiedImageError

from app.infra.fetch import FetchError

if TYPE_CHECKING:
    from app.infra.fetch import Fetcher
    from app.infra.storage import ObjectStore
    from app.repos.base import Link, LinkRepo

SIZE = 70
HIDPI = 2
MAX_PAGE_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# Pillow refuses images claiming more pixels than this, which guards against decompression bombs.
MAX_PIXELS = 40_000_000
# Of a page's own <img> tags, r2 weighed these many at most here.
MAX_CANDIDATES = 8
MIN_AREA = 5000
MAX_RATIO = 1.5


class _Images(HTMLParser):
    """The image hints in a page: its Open Graph image, image_src link, and <img> tags."""

    def __init__(self) -> None:
        super().__init__()
        self.og_image: str | None = None
        self.og_image_url: str | None = None
        self.image_src: str | None = None
        self.imgs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = {name: value or '' for name, value in attrs}
        if tag == 'meta':
            key = found.get('property') or found.get('name') or ''
            if key == 'og:image' and found.get('content') and self.og_image is None:
                self.og_image = found['content']
            if key == 'og:image:url' and found.get('content') and self.og_image_url is None:
                self.og_image_url = found['content']
        elif tag == 'link' and found.get('rel') == 'image_src' and found.get('href'):
            self.image_src = self.image_src or found['href']
        elif tag == 'img' and found.get('src') and len(self.imgs) < MAX_CANDIDATES:
            self.imgs.append(found['src'])


Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def _open(data: bytes) -> Image.Image:
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        image = Image.open(io.BytesIO(data))
        image.load()
    return image


def _size(data: bytes) -> tuple[int, int] | None:
    try:
        return _open(data).size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Warning, ValueError):
        return None


async def _largest(fetcher: Fetcher, page: str, sources: list[str]) -> bytes | None:
    """r2's choice without hints: the biggest image that is not tiny, long, or a sprite."""
    best, best_area = None, 0.0
    for source in sources:
        url = urljoin(page, source)
        try:
            fetched = await fetcher.get(url, limit=MAX_IMAGE_BYTES)
        except FetchError:
            continue
        size = _size(fetched.body)
        if size is None or min(size) == 0:
            continue
        area: float = size[0] * size[1]
        if area < MIN_AREA or max(size) / min(size) > MAX_RATIO:
            continue
        if 'sprite' in url.lower():
            area /= 10
        if area > best_area:
            best, best_area = fetched.body, area
    return best


async def find_image(fetcher: Fetcher, url: str) -> bytes | None:
    """The image a link should be shown with, or None."""
    try:
        fetched = await fetcher.get(url, limit=MAX_IMAGE_BYTES)
    except FetchError:
        return None
    if fetched.content_type.startswith('image/'):
        return fetched.body
    if 'html' not in fetched.content_type:
        return None
    hints = _Images()
    hints.feed(fetched.body[:MAX_PAGE_BYTES].decode('utf-8', 'replace'))
    chosen = hints.og_image or hints.og_image_url or hints.image_src
    if chosen:
        try:
            return (await fetcher.get(urljoin(fetched.url, chosen), limit=MAX_IMAGE_BYTES)).body
        except FetchError:
            return None
    return await _largest(fetcher, fetched.url, hints.imgs)


def _entropy(image: Image.Image) -> float:
    histogram = image.histogram()
    total = sum(histogram)
    return -sum((h / total) * math.log2(h / total) for h in histogram if h)


def _square(image: Image.Image) -> Image.Image:
    """r2's crop: while taller than wide, cut 10 pixels from whichever end has less detail."""
    width, height = image.size
    while height > width:
        cut = min(height - width, 10)
        top = image.crop((0, 0, width, cut))
        bottom = image.crop((0, height - cut, width, height))
        if _entropy(bottom) < _entropy(top):
            image = image.crop((0, 0, width, height - cut))
        else:
            image = image.crop((0, cut, width, height))
        width, height = image.size
    return image


@dataclass(frozen=True, slots=True)
class Thumbnail:
    jpeg: bytes
    # The size to show it at; the file may be twice that, for high-density screens.
    width: int
    height: int

    @property
    def key(self) -> str:
        return f'thumbs/{hashlib.sha256(self.jpeg).hexdigest()[:24]}.jpg'


def make_thumbnail(data: bytes) -> Thumbnail | None:
    try:
        image = _open(data)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Warning, ValueError):
        return None
    image = ImageOps.exif_transpose(image)
    if image.mode != 'RGB':
        # JPEG has no transparency: lay the image on white.
        background = Image.new('RGB', image.size, 'white')
        rgba = image.convert('RGBA')
        background.paste(rgba, mask=rgba.getchannel('A'))
        image = background
    image = _square(image)
    scale = HIDPI if image.size[0] >= SIZE * HIDPI else 1
    image.thumbnail((SIZE * scale, SIZE * scale))
    out = io.BytesIO()
    image.save(out, format='JPEG', quality=85)
    return Thumbnail(out.getvalue(), image.size[0] // scale, image.size[1] // scale)


async def thumbnail_link(
    links: LinkRepo, store: ObjectStore, fetcher: Fetcher, link: Link
) -> Thumbnail | None:
    """Make and store the link's thumbnail, and record it; None when there is none to make."""
    if link.is_self or link.thumbnail:
        return None
    data = await find_image(fetcher, link.url)
    made = make_thumbnail(data) if data else None
    if made is None:
        return None
    await store.put(made.key, made.jpeg, 'image/jpeg')
    await links.set_thumbnail(link.id, made.key, (made.width, made.height))
    return made
