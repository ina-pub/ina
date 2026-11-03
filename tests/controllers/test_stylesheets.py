from __future__ import annotations

import io
import os
import re
from typing import TYPE_CHECKING, Any

from PIL import Image

from tests.controllers.test_communities import create
from tests.controllers.test_moderation import act, modlog
from tests.helpers import codes, register

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import AsyncTestClient


def png(width: int = 4, height: int = 3, colour: str = 'red') -> bytes:
    out = io.BytesIO()
    Image.new('RGB', (width, height), colour).save(out, format='PNG')
    return out.getvalue()


async def setup(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> tuple[str, str]:
    alice: str = (await register(client, 'alice'))['data']['modhash']
    await create(client, alice)
    bob: str = (await register(other_client, 'bob'))['data']['modhash']
    return alice, bob


async def upload(client: AsyncTestClient[Litestar], modhash: str, data: bytes, **form: str) -> Any:  # noqa: ANN401
    return await client.post(
        '/in/pics/api/upload_community_img',
        data={'uh': modhash, 'api_type': 'json', 'upload_type': 'img', **form},
        files={'file': ('image.png', data, 'image/png')},
    )


async def save(client: AsyncTestClient[Litestar], modhash: str, css: str) -> Any:  # noqa: ANN401
    return await act(client, '/in/pics/api/community_stylesheet', modhash, stylesheet_contents=css)


async def test_images_and_the_stylesheet(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, bob = await setup(client, other_client)
    assert (await other_client.get('/in/pics/about/stylesheet')).status_code == 403
    refused = await save(other_client, bob, 'a { color: red }')
    assert refused.status_code == 403

    uploaded = (await upload(client, alice, png(), name='banner')).json()['json']
    assert uploaded['errors'] == []
    src = uploaded['data']['img_src']
    assert re.fullmatch(r'/media/community/1/[0-9a-f]{24}\.png', src)
    image = await other_client.get(src)
    assert image.content == png()
    assert image.headers['content-type'] == 'image/png'
    assert image.headers['cache-control'] == 'public, max-age=31536000, immutable'
    editor = (await client.get('/in/pics/about/stylesheet.json')).json()['data']
    assert editor['images'] == [{'name': 'banner', 'url': src, 'link': 'url(%%banner%%)'}]

    saved = await save(client, alice, '#header {\n  background: url(%%banner%%);\n}')
    assert saved.json()['json']['errors'] == []
    page = (await other_client.get('/in/pics/')).text
    found = re.search(r'href="(/in/pics/stylesheet\.css\?v=[0-9a-f]{16})"', page)
    assert found is not None
    served = await other_client.get(found[1])
    assert served.text == f'#header{{background:url({src})}}'
    assert served.headers['cache-control'] == 'public, max-age=31536000, immutable'
    stale = await other_client.get('/in/pics/stylesheet.css?v=old')
    assert stale.headers['cache-control'] == 'public, max-age=60'
    # Its source is the config/stylesheet wiki page, with a history.
    history = (await client.get('/in/pics/wiki/revisions/config/stylesheet.json')).json()
    assert len(history['data']['children']) == 1
    log = (await client.get('/in/pics/about/log.json')).json()['data']['children']
    assert [(a['data']['action'], a['data']['details']) for a in log] == [
        ('wikirevise', 'changed the stylesheet'),
        ('editsettings', 'upload_image'),
    ]

    # A viewer who turned community styles off does not get it.
    await other_client.post('/post/options', data={'uh': bob, 'numsites': '25'})
    assert 'stylesheet.css' not in (await other_client.get('/in/pics/')).text


async def test_a_stylesheet_with_errors_is_not_saved(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client)
    bad = await save(client, alice, 'a { color: red }\nb { background: url(http://x.example/a) }')
    body = bad.json()['json']
    assert codes(body) == [('SPECIAL_ERRORS', 'content')]
    assert 'line 2: url() may only name an uploaded image' in body['errors'][0][1]
    assert (await client.get('/in/pics/stylesheet.css')).status_code == 404
    assert 'stylesheet.css' not in (await client.get('/in/pics/')).text
    checked = await act(
        client,
        '/in/pics/api/community_stylesheet',
        alice,
        stylesheet_contents='a { color: red }',
        op='preview',
    )
    assert checked.json()['json']['errors'] == []
    assert (await client.get('/in/pics/stylesheet.css')).status_code == 404
    assert await modlog(client) == []


async def test_image_rules(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client)

    async def errors(data: bytes, name: str = 'x') -> list[tuple[Any, Any]]:
        return codes((await upload(client, alice, data, name=name)).json()['json'])

    assert await errors(b'not an image') == [('IMAGE_ERROR', 'file')]
    assert await errors(png(), name='no spaces') == [('BAD_CSS_NAME', 'name')]
    # Noise does not compress: 500 by 500 is about 750 KiB as a PNG.
    noise = Image.frombytes('RGB', (500, 500), os.urandom(500 * 500 * 3))
    big = io.BytesIO()
    noise.save(big, format='PNG')
    assert await errors(big.getvalue()) == [('IMAGE_ERROR', 'file')]
    gif = io.BytesIO()
    Image.new('RGB', (2, 2)).save(gif, format='GIF')
    assert await errors(gif.getvalue()) == [('IMAGE_ERROR', 'file')]
    # The same name replaces the image.
    await upload(client, alice, png(colour='red'), name='x')
    await upload(client, alice, png(colour='blue'), name='x')
    images = (await client.get('/in/pics/about/stylesheet.json')).json()['data']['images']
    assert [i['name'] for i in images] == ['x']
    await act(client, '/in/pics/api/delete_community_img', alice, img_name='x')
    assert (await client.get('/in/pics/about/stylesheet.json')).json()['data']['images'] == []
    assert (await client.get('/media/../etc/passwd')).status_code == 404


async def test_header_image(
    client: AsyncTestClient[Litestar], other_client: AsyncTestClient[Litestar]
) -> None:
    alice, _ = await setup(client, other_client)
    response = await upload(
        client, alice, png(120, 40), upload_type='header', header_title='our pics'
    )
    src = response.json()['json']['data']['img_src']
    page = (await other_client.get('/in/pics/')).text
    assert f'src="{src}"' in page
    assert 'width="120" height="40"' in page
    assert 'title="our pics"' in page
    await act(client, '/in/pics/api/delete_community_header', alice)
    assert 'id="header-img" class="default-header"' in (await other_client.get('/in/pics/')).text
