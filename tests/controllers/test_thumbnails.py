from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Any

from litestar.testing import AsyncTestClient
from PIL import Image

from app.main import create_app
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.helpers import Pages, register, run_published
from tests.services.test_media import image

if TYPE_CHECKING:
    from dramatiq import Message

    from app.settings import Settings


async def test_new_links_get_thumbnails(
    settings: Settings, server: str, published: list[Message[Any]]
) -> None:
    Pages.routes = {
        '/page': (200, 'text/html', b'<meta property="og:image" content="/photo.png">'),
        '/photo.png': (200, 'image/png', image(300, 200, 'blue')),
    }
    app = create_app(settings.model_copy(update={'thumbnails': True}))
    async with AsyncTestClient(app=app) as client:
        alice: str = (await register(client, 'alice'))['data']['modhash']
        await create(client, alice, show_media='on')
        await create(client, alice, name='plain', title='plain')
        await submitted(client, alice, title='with a picture', url=f'{server}/page')
        await submitted(client, alice, community='plain', title='elsewhere', url=f'{server}/page')
        await submitted(client, alice, kind='self', title='just words', text='hi')
        ran = await run_published(published)
        assert ran.count('make_thumbnail') == 2

        data = (await client.get('/in/pics/new.json')).json()['data']['children']
        thumbs = {d['data']['title']: d['data']['thumbnail'] for d in data}
        assert thumbs['just words'] == 'self'
        url = thumbs['with a picture']
        assert re.fullmatch(r'/media/thumbs/[0-9a-f]{24}\.jpg', url)
        made = Image.open(io.BytesIO((await client.get(url)).content))
        # Wide, so not cropped: shrunk to 140 across, for high-density screens.
        assert made.size == (140, 93)
        link = next(d['data'] for d in data if d['data']['title'] == 'with a picture')
        assert (link['thumbnail_width'], link['thumbnail_height']) == (70, 46)
        assert f'<img src="{url}"' in (await client.get('/in/pics/')).text
        # A community that does not show media keeps r2's default picture.
        plain = (await client.get('/in/plain.json')).json()['data']['children'][0]['data']
        assert plain['thumbnail'] == 'default'
