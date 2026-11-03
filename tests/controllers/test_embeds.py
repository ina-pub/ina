from __future__ import annotations

from typing import TYPE_CHECKING, Any

from litestar.testing import AsyncTestClient

from app.main import create_app
from tests.controllers.test_communities import create
from tests.controllers.test_listings import submitted
from tests.helpers import post_json, register, run_published

if TYPE_CHECKING:
    from dramatiq import Message

    from app.settings import Settings

VIDEO = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=43'
PLAYER = 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0&start=43'


def by_title(listing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c['data']['title']: c['data'] for c in listing['data']['children']}


async def test_links_to_listed_sites_open_in_their_player(
    settings: Settings, published: list[Message[Any]]
) -> None:
    app = create_app(
        settings.model_copy(update={'embeds': True, 'site_url': 'https://ina.example'})
    )
    async with AsyncTestClient(app=app) as client:
        alice: str = (await register(client, 'alice'))['data']['modhash']
        await create(client, alice)
        await submitted(client, alice, title='a video', url=VIDEO)
        await submitted(client, alice, title='a clip', url='https://clips.twitch.tv/Slug')
        await submitted(client, alice, title='a file', url='https://example.com/clip.webm')
        await submitted(client, alice, title='a page', url='https://example.com/page')
        await submitted(client, alice, kind='self', title='words', text='hi')
        ran = await run_published(published)
        # Self posts have nothing to embed, so no job is sent for them.
        assert ran.count('find_embed') == 4

        links = by_title((await client.get('/in/pics/new.json')).json())
        video = links['a video']
        assert video['media']['type'] == 'youtube.com'
        assert f'src="{PLAYER.replace("&", "&amp;")}"' in video['media']['oembed']['html']
        assert video['secure_media'] == video['media']
        assert video['media_embed']['height'] == 360
        assert video['secure_media_embed'] == video['media_embed']
        for title in ('a file', 'a page', 'words'):
            assert links[title]['media'] is None
            assert links[title]['media_embed'] == {}

        page = (await client.get('/in/pics/')).text
        # The player is written into the page, but loads only when the expando opens.
        assert f'<iframe data-src="{PLAYER.replace("&", "&amp;")}"' in page
        assert 'class="expando-button collapsed video"' in page
        assert 'parent=ina.example' in page
        assert '<video data-src="https://example.com/clip.webm"' in page
        csp = (await client.get('/in/pics/')).headers['content-security-policy']
        assert 'frame-src https://www.youtube-nocookie.com ' in csp
        assert 'https://clips.twitch.tv https://player.twitch.tv' in csp
        assert "media-src 'self' https:" in csp


async def test_nsfw_media_opens_only_for_viewers_over_eighteen(
    settings: Settings, published: list[Message[Any]]
) -> None:
    with_embeds = settings.model_copy(update={'embeds': True})
    async with (
        AsyncTestClient(app=create_app(with_embeds)) as client,
        AsyncTestClient(app=create_app(with_embeds)) as other,
        AsyncTestClient(app=create_app(with_embeds)) as visitor,
    ):
        alice: str = (await register(client, 'alice'))['data']['modhash']
        bob: str = (await register(other, 'bob'))['data']['modhash']
        await create(client, alice)
        name = await submitted(client, alice, title='racy', url='https://redgifs.com/watch/abc')
        await submitted(client, alice, title='racy picture', url='https://example.com/a.png')
        await run_published(published)
        for link in (name, 't3_2'):
            body = await post_json(client, '/api/marknsfw', {'uh': alice, 'id': link})
            assert body['errors'] == []

        def buttons(page: str) -> int:
            return page.count('class="expando-button')

        # Signed out, or signed in without saying so, neither opens in place.
        assert buttons((await visitor.get('/in/pics/')).text) == 0
        assert buttons((await other.get('/in/pics/')).text) == 0
        form = {'uh': bob, 'over_18': 'on'}
        await other.post('/post/options', data=form, follow_redirects=False)
        page = (await other.get('/in/pics/')).text
        assert buttons(page) == 2
        assert 'https://www.redgifs.com/ifr/abc' in page
        # The API reports the player either way, as r2's did; the label is for clients to heed.
        racy = by_title((await other.get('/in/pics/new.json')).json())['racy']
        assert racy['over_18'] is True
        assert racy['media']['type'] == 'redgifs.com'
