from __future__ import annotations

import io

import pytest
from PIL import Image

from app.infra import fetch
from app.infra.fetch import Fetcher, FetchError
from app.services.media import find_image, make_thumbnail
from tests.helpers import Pages


def image(
    width: int,
    height: int,
    colour: str | tuple[int, ...] = 'red',
    mode: str = 'RGB',
    kind: str = 'PNG',
) -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), colour).save(out, format=kind)
    return out.getvalue()


def test_thumbnails_are_squared_and_shrunk() -> None:
    tall = make_thumbnail(image(100, 300))
    assert tall is not None
    assert (tall.width, tall.height) == (70, 70)
    assert Image.open(io.BytesIO(tall.jpeg)).size == (70, 70)
    # Wide images are not cropped, as on r2; big ones are kept at twice the size, for HiDPI.
    wide = make_thumbnail(image(400, 200))
    assert wide is not None
    assert (wide.width, wide.height) == (70, 35)
    assert Image.open(io.BytesIO(wide.jpeg)).size == (140, 70)
    assert wide.key.startswith('thumbs/')
    assert wide.key.endswith('.jpg')
    clear = make_thumbnail(image(80, 80, colour=(0, 0, 0, 0), mode='RGBA'))
    assert clear is not None
    assert Image.open(io.BytesIO(clear.jpeg)).getpixel((5, 5)) == (255, 255, 255)
    assert make_thumbnail(b'not an image') is None


# A page whose Open Graph image wins over its image_src link.
HINTED = (
    b'<html><head><link rel="image_src" href="/tiny.png">'
    b'<meta property="og:image" content="/og.png"></head></html>'
)


async def test_choosing_the_image(server: str) -> None:
    og = image(300, 300, 'blue')
    big = image(200, 180, 'green')
    Pages.routes = {
        '/photo.png': (200, 'image/png', image(50, 50)),
        '/og.png': (200, 'image/png', og),
        '/big.png': (200, 'image/png', big),
        '/tiny.png': (200, 'image/png', image(10, 10)),
        '/long.png': (200, 'image/png', image(600, 100)),
        '/hinted': (200, 'text/html', HINTED),
        '/plain': (
            200,
            'text/html; charset=utf-8',
            b'<img src="/tiny.png"><img src="/long.png"><img src="/big.png">',
        ),
        '/moved': (302, 'text/plain', b'/photo.png'),
    }
    fetcher = Fetcher(allow_private=True)
    assert await find_image(fetcher, f'{server}/photo.png') == image(50, 50)
    assert await find_image(fetcher, f'{server}/hinted') == og
    # Without hints: the largest image that is neither tiny nor long.
    assert await find_image(fetcher, f'{server}/plain') == big
    assert await find_image(fetcher, f'{server}/moved') == image(50, 50)
    assert await find_image(fetcher, f'{server}/nothing') is None
    await fetcher.aclose()


async def test_links_cannot_reach_inside(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    strict = Fetcher()
    with pytest.raises(FetchError, match='not a public address'):
        await strict.get(f'{server}/anything', limit=1000)
    with pytest.raises(FetchError, match='not an http url'):
        await strict.get('file:///etc/passwd', limit=1000)
    # Every redirect is checked too: let this test's server count as public, then send a
    # redirect to a private address.
    monkeypatch.setattr(fetch, '_public', lambda address: address == '127.0.0.1')
    Pages.routes = {
        '/inside': (302, 'text/plain', b'http://10.0.0.1/admin'),
        '/huge': (200, 'image/png', b'x' * 5000),
    }
    with pytest.raises(FetchError, match=r'10\.0\.0\.1 is not a public address'):
        await strict.get(f'{server}/inside', limit=1000)
    with pytest.raises(FetchError, match='larger than 1000 bytes'):
        await strict.get(f'{server}/huge', limit=1000)
    await strict.aclose()
