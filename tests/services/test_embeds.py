from __future__ import annotations

import json

import pytest

from app.infra.fetch import Fetcher
from app.repos.base import Embed
from app.services import embeds
from app.services.embeds import find, match, player, video_source
from tests.helpers import Pages

BSKY_DID = 'did:plc:z72i7hdynmk6r22z27h6tvur'


@pytest.mark.parametrize(
    ('url', 'expected'),
    [
        ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', Embed('youtube', 'dQw4w9WgXcQ')),
        ('https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s', Embed('youtube', 'dQw4w9WgXcQ', 90)),
        ('https://youtu.be/dQw4w9WgXcQ?t=43', Embed('youtube', 'dQw4w9WgXcQ', 43)),
        ('https://youtube.com/shorts/dQw4w9WgXcQ', Embed('youtube', 'dQw4w9WgXcQ')),
        ('https://www.youtube.com/live/dQw4w9WgXcQ', Embed('youtube', 'dQw4w9WgXcQ')),
        ('https://music.youtube.com/watch?v=dQw4w9WgXcQ', Embed('youtube', 'dQw4w9WgXcQ')),
        ('https://vimeo.com/76979871', Embed('vimeo', '76979871')),
        ('https://vimeo.com/76979871/8272103f6e', Embed('vimeo', '76979871:8272103f6e')),
        ('https://vimeo.com/channels/staffpicks/76979871#t=30s', Embed('vimeo', '76979871', 30)),
        (
            'https://player.vimeo.com/video/76979871?h=8272103f6e',
            Embed('vimeo', '76979871:8272103f6e'),
        ),
        ('https://streamable.com/moo', Embed('streamable', 'moo')),
        ('https://streamable.com/e/moo', Embed('streamable', 'moo')),
        ('https://clips.twitch.tv/FunnyClipSlug', Embed('twitch', 'clip:FunnyClipSlug')),
        ('https://www.twitch.tv/someone/clip/FunnyClipSlug', Embed('twitch', 'clip:FunnyClipSlug')),
        ('https://www.twitch.tv/videos/123456?t=1h2m3s', Embed('twitch', 'video:123456', 3723)),
        ('https://www.twitch.tv/Someone', Embed('twitch', 'channel:someone')),
        ('https://www.redgifs.com/watch/SomeWordsHere', Embed('redgifs', 'somewordshere')),
        (
            'https://www.tiktok.com/@user/video/7234567890123456789',
            Embed('tiktok', '7234567890123456789'),
        ),
        ('https://m.tiktok.com/v/7234567890123456789.html', Embed('tiktok', '7234567890123456789')),
        ('https://soundcloud.com/forss/flickermood', Embed('soundcloud', 'forss/flickermood')),
        ('https://soundcloud.com/forss/sets/soulhack', Embed('soundcloud', 'forss/sets/soulhack')),
        (
            'https://tenor.com/view/happy-dance-gif-1034066229473654461',
            Embed('tenor', '1034066229473654461'),
        ),
        ('https://tenor.com/en-GB/view/happy-dance-gif-12345678', Embed('tenor', '12345678')),
        (
            'https://giphy.com/gifs/cat-funny-3o7aD2saalBwwftBIY',
            Embed('giphy', '3o7aD2saalBwwftBIY'),
        ),
        ('https://twitter.com/jack/status/20', Embed('twitter', '20')),
        ('https://x.com/jack/status/20/photo/1', Embed('twitter', '20')),
        ('https://mobile.twitter.com/i/web/status/20', Embed('twitter', '20')),
        (
            'https://bsky.app/profile/bsky.app/post/3l6oveex3ii2l',
            Embed('bluesky', 'bsky.app/3l6oveex3ii2l'),
        ),
        (f'https://bsky.app/profile/{BSKY_DID}/post/3l6o', Embed('bluesky', f'{BSKY_DID}/3l6o')),
        ('https://www.instagram.com/p/C1a2b3c4d5e/', Embed('instagram', 'C1a2b3c4d5e')),
        ('https://www.instagram.com/someone/reel/C1a2b3c4d5e/', Embed('instagram', 'C1a2b3c4d5e')),
        ('https://www.threads.net/@zuck/post/C1a2b3c4d5e', Embed('threads', 'zuck/C1a2b3c4d5e')),
        (
            'https://www.threads.com/@zuck/post/C1a2b3c4d5e/media',
            Embed('threads', 'zuck/C1a2b3c4d5e'),
        ),
        # Pages on those sites that are not one post or video, and other sites.
        ('https://www.youtube.com/@channel', None),
        ('https://www.youtube.com/watch?v=short', None),
        ('https://vimeo.com/about', None),
        ('https://www.twitch.tv/directory', None),
        ('https://soundcloud.com/discover', None),
        ('https://soundcloud.com/forss/likes', None),
        ('https://x.com/jack', None),
        ('https://bsky.app/profile/bsky.app', None),
        ('https://www.tiktok.com/@user', None),
        ('https://example.com/watch?v=dQw4w9WgXcQ', None),
        ('ftp://youtube.com/watch?v=dQw4w9WgXcQ', None),
        ('https://[broken/', None),
    ],
)
def test_links_are_matched_to_their_site(url: str, expected: Embed | None) -> None:
    assert match(url) == expected


def test_players() -> None:
    def src(embed: Embed) -> str:
        made = player(embed, 'ina.example')
        assert made is not None
        return made.src

    assert src(Embed('youtube', 'dQw4w9WgXcQ', 43)) == (
        'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0&start=43'
    )
    assert src(Embed('vimeo', '76979871:8272103f6e', 30)) == (
        'https://player.vimeo.com/video/76979871?dnt=1&h=8272103f6e#t=30s'
    )
    # Twitch plays only on the site it is told it is on.
    assert src(Embed('twitch', 'clip:Slug')) == (
        'https://clips.twitch.tv/embed?clip=Slug&parent=ina.example&autoplay=false'
    )
    assert src(Embed('twitch', 'video:123', 3723)) == (
        'https://player.twitch.tv/?video=v123&parent=ina.example&autoplay=false&time=1h2m3s'
    )
    assert src(Embed('soundcloud', 'forss/flickermood')).startswith(
        'https://w.soundcloud.com/player/?url=https%3A%2F%2Fsoundcloud.com%2Fforss%2Fflickermood&'
    )
    assert src(Embed('bluesky', f'{BSKY_DID}/3l6o')) == (
        f'https://embed.bsky.app/embed/{BSKY_DID}/app.bsky.feed.post/3l6o?id=3l6o'
    )
    assert src(Embed('threads', 'zuck/C1a')) == 'https://www.threads.com/@zuck/post/C1a/embed'
    assert player(Embed('gone', 'x'), 'ina.example') is None


def test_every_player_loads_from_an_allowed_frame_source() -> None:
    for provider in embeds.PROVIDERS.values():
        for embed_id in ('a:b', 'a/b', 'abc'):
            made = player(Embed(provider.key, embed_id), 'ina.example')
            assert made is not None
            assert any(made.src.startswith(f'{frame}/') for frame in embeds.FRAME_SOURCES)


def test_api_media_follows_r2() -> None:
    made = player(Embed('youtube', 'dQw4w9WgXcQ'), 'ina.example')
    assert made is not None
    media = made.oembed()
    assert media['type'] == 'youtube.com'
    oembed = media['oembed']
    assert isinstance(oembed, dict)
    assert oembed['provider_name'] == 'YouTube'
    assert oembed['type'] == 'video'
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"' in made.iframe
    assert made.media_embed() == {
        'content': made.iframe,
        'width': 640,
        'height': 360,
        'scrolling': False,
    }


def test_video_files() -> None:
    assert video_source('https://example.com/clip.MP4') == 'https://example.com/clip.MP4'
    assert video_source('https://i.imgur.com/abc123.gifv') == 'https://i.imgur.com/abc123.mp4'
    # Over https only, as the page's CSP allows.
    assert video_source('http://example.com/clip.mp4') is None
    assert video_source('https://example.com/clip.gifv') is None
    assert video_source('https://example.com/page') is None


async def test_short_links_are_followed(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    Pages.routes = {
        '/t/abc': (302, 'text/html', b'https://www.tiktok.com/@user/video/7234567890123456789')
    }
    monkeypatch.setattr(embeds, 'SHORT_HOSTS', frozenset({'127.0.0.1'}))
    fetcher = Fetcher(allow_private=True)
    try:
        # The TikTok page itself is never fetched; where the short link points is enough.
        assert await find(fetcher, f'{server}/t/abc') == Embed('tiktok', '7234567890123456789')
        assert await find(fetcher, f'{server}/t/missing') is None
    finally:
        await fetcher.aclose()


async def test_bluesky_handles_become_dids(server: str) -> None:
    path = '/xrpc/com.atproto.identity.resolveHandle?handle='
    Pages.routes = {
        f'{path}bsky.app': (200, 'application/json', json.dumps({'did': BSKY_DID}).encode()),
        f'{path}odd.example': (200, 'application/json', b'{"did": "not a did"}'),
    }
    fetcher = Fetcher(allow_private=True)
    try:
        found = await find(fetcher, 'https://bsky.app/profile/bsky.app/post/3l6o', appview=server)
        assert found == Embed('bluesky', f'{BSKY_DID}/3l6o')
        odd = 'https://bsky.app/profile/odd.example/post/3l6o'
        assert await find(fetcher, odd, appview=server) is None
        gone = 'https://bsky.app/profile/gone.example/post/3l6o'
        assert await find(fetcher, gone, appview=server) is None
    finally:
        await fetcher.aclose()
