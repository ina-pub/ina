"""Players for links to video, audio, GIFs and posts on other sites, opened in a link's expando.

Only the sites listed here are embedded, and each only through an iframe on that site's own
player page. None of their scripts run on ina's pages, and the page's CSP ``frame-src`` is made
from this list, so a site added here must be added nowhere else.

Finding a link's embed happens once, in the media job, because a few sites need a request first:
short links have to be followed, and a Bluesky post is embedded by its author's DID, not the
handle in its URL. What is stored is only the site and the post's ID; the player's address is
built when the page is drawn, so a site changing its player URL needs no data migration.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from app.infra.fetch import FetchError
from app.repos.base import Embed

if TYPE_CHECKING:
    from collections.abc import Callable
    from urllib.parse import SplitResult

    from app.infra.fetch import Fetcher
    from app.jsontypes import JSON

BLUESKY_APPVIEW = 'https://public.api.bsky.app'
MAX_HANDLE_REPLY = 4096
# Sites whose short links only say where the post is once they are followed.
SHORT_HOSTS = frozenset({'on.soundcloud.com', 'vm.tiktok.com', 'vt.tiktok.com'})


@dataclass(frozen=True, slots=True)
class Provider:
    key: str
    name: str
    # The site's own address, for the API's ``media.type`` and ``provider_url``.
    domain: str
    # What the expando shows (video, audio, gif or post), which picks its button and label.
    kind: str
    # How the frame is laid out: wide, tall, audio, gif or post. See 20-things.css.
    layout: str
    # The size the API reports; pages size frames by their layout instead.
    width: int
    height: int
    # Where its frames load from, for the CSP.
    frames: tuple[str, ...]
    src: Callable[[Embed, str], str]
    # Its frames tell the page their height, so the frame can grow to fit the post.
    resizes: bool = False


@dataclass(frozen=True, slots=True)
class Player:
    """A link's embed, ready to show: which site, and the address of its player."""

    provider: Provider
    src: str

    @property
    def iframe(self) -> str:
        """The frame as HTML, for API clients that show embeds themselves."""
        p = self.provider
        return (
            f'<iframe src="{html.escape(self.src)}" width="{p.width}" height="{p.height}" '
            f'title="{html.escape(p.name)}" frameborder="0" scrolling="no" allowfullscreen '
            'allow="autoplay; encrypted-media; fullscreen; picture-in-picture"></iframe>'
        )

    def oembed(self) -> dict[str, JSON]:
        """r2's ``media`` value: the site, and an oEmbed answer describing the player."""
        p = self.provider
        return {
            'type': p.domain,
            'oembed': {
                'version': '1.0',
                'type': 'video' if p.kind in {'video', 'gif'} else 'rich',
                'provider_name': p.name,
                'provider_url': f'https://{p.domain}/',
                'html': self.iframe,
                'width': p.width,
                'height': p.height,
            },
        }

    def media_embed(self) -> dict[str, JSON]:
        """r2's ``media_embed`` value."""
        p = self.provider
        return {'content': self.iframe, 'width': p.width, 'height': p.height, 'scrolling': False}


# Each site's player address, from what was stored and this site's host name.


def _youtube(embed: Embed, _: str) -> str:
    query = {'rel': '0'} | ({'start': str(embed.start)} if embed.start else {})
    return f'https://www.youtube-nocookie.com/embed/{embed.id}?{urlencode(query)}'


def _vimeo(embed: Embed, _: str) -> str:
    video, _sep, unlisted = embed.id.partition(':')
    query = {'dnt': '1'} | ({'h': unlisted} if unlisted else {})
    start = f'#t={embed.start}s' if embed.start else ''
    return f'https://player.vimeo.com/video/{video}?{urlencode(query)}{start}'


def _twitch(embed: Embed, host: str) -> str:
    # Twitch refuses to play unless told which site the frame is on.
    kind, _sep, name = embed.id.partition(':')
    if kind == 'clip':
        query = {'clip': name, 'parent': host, 'autoplay': 'false'}
        return f'https://clips.twitch.tv/embed?{urlencode(query)}'
    if kind == 'video':
        query = {'video': f'v{name}', 'parent': host, 'autoplay': 'false'}
        if embed.start:
            hours, rest = divmod(embed.start, 3600)
            query['time'] = f'{hours}h{rest // 60}m{rest % 60}s'
    else:
        query = {'channel': name, 'parent': host, 'autoplay': 'false'}
    return f'https://player.twitch.tv/?{urlencode(query)}'


def _soundcloud(embed: Embed, _: str) -> str:
    track = quote(f'https://soundcloud.com/{embed.id}', safe='')
    return f'https://w.soundcloud.com/player/?url={track}&visual=false&show_comments=false'


def _bluesky(embed: Embed, _: str) -> str:
    did, _sep, rkey = embed.id.partition('/')
    # The frame names itself in its height messages with this id; the page matches on the
    # frame instead, but the frame only reports when given one.
    return f'https://embed.bsky.app/embed/{did}/app.bsky.feed.post/{rkey}?id={rkey}'


def _threads(embed: Embed, _: str) -> str:
    user, _sep, code = embed.id.partition('/')
    return f'https://www.threads.com/@{user}/post/{code}/embed'


def _simple(template: str) -> Callable[[Embed, str], str]:
    return lambda embed, _: template.format(id=embed.id)


PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider(
            'youtube', 'YouTube', 'youtube.com', 'video', 'wide', 640, 360,
            ('https://www.youtube-nocookie.com',), _youtube,
        ),
        Provider(
            'vimeo', 'Vimeo', 'vimeo.com', 'video', 'wide', 640, 360,
            ('https://player.vimeo.com',), _vimeo,
        ),
        Provider(
            'streamable', 'Streamable', 'streamable.com', 'video', 'wide', 640, 360,
            ('https://streamable.com',), _simple('https://streamable.com/o/{id}'),
        ),
        Provider(
            'twitch', 'Twitch', 'twitch.tv', 'video', 'wide', 640, 360,
            ('https://clips.twitch.tv', 'https://player.twitch.tv'), _twitch,
        ),
        Provider(
            'redgifs', 'RedGIFs', 'redgifs.com', 'video', 'wide', 640, 360,
            ('https://www.redgifs.com',), _simple('https://www.redgifs.com/ifr/{id}'),
        ),
        Provider(
            'tiktok', 'TikTok', 'tiktok.com', 'video', 'tall', 325, 578,
            ('https://www.tiktok.com',), _simple('https://www.tiktok.com/player/v1/{id}'),
        ),
        Provider(
            'soundcloud', 'SoundCloud', 'soundcloud.com', 'audio', 'audio', 640, 166,
            ('https://w.soundcloud.com',), _soundcloud,
        ),
        Provider(
            'tenor', 'Tenor', 'tenor.com', 'gif', 'gif', 480, 360,
            ('https://tenor.com',), _simple('https://tenor.com/embed/{id}'),
        ),
        Provider(
            'giphy', 'GIPHY', 'giphy.com', 'gif', 'gif', 480, 360,
            ('https://giphy.com',), _simple('https://giphy.com/embed/{id}'),
        ),
        Provider(
            'twitter', 'X', 'x.com', 'post', 'post', 550, 500,
            ('https://platform.twitter.com',),
            _simple('https://platform.twitter.com/embed/Tweet.html?id={id}&dnt=true'),
            resizes=True,
        ),
        Provider(
            'bluesky', 'Bluesky', 'bsky.app', 'post', 'post', 550, 500,
            ('https://embed.bsky.app',), _bluesky, resizes=True,
        ),
        Provider(
            'instagram', 'Instagram', 'instagram.com', 'post', 'post', 550, 700,
            ('https://www.instagram.com',),
            _simple('https://www.instagram.com/p/{id}/embed/captioned/'),
            resizes=True,
        ),
        Provider(
            'threads', 'Threads', 'threads.com', 'post', 'post', 550, 500,
            # threads.net redirects there inside the frame, so both are allowed.
            ('https://www.threads.com', 'https://www.threads.net'), _threads, resizes=True,
        ),
    )
}  # fmt: skip
FRAME_SOURCES = tuple(dict.fromkeys(f for p in PROVIDERS.values() for f in p.frames))


def player(embed: Embed, site_host: str) -> Player | None:
    """The player for a stored embed; None for a site no longer listed."""
    provider = PROVIDERS.get(embed.provider)
    if provider is None:
        return None
    return Player(provider, provider.src(embed, site_host))


# Links straight to a video file play in a <video>, as links to images open in an <img>.
VIDEO_SUFFIXES = ('.mp4', '.webm', '.m4v')


def video_source(url: str) -> str | None:
    """What a <video> should play for a link to a video file, or None. Over https only."""
    parts = urlsplit(url)
    if parts.scheme != 'https':
        return None
    path = parts.path.lower()
    if path.endswith(VIDEO_SUFFIXES):
        return url
    # Imgur's .gifv is a page around an .mp4 of the same name.
    if parts.hostname == 'i.imgur.com' and path.endswith('.gifv'):
        return f'https://i.imgur.com{parts.path[: -len(".gifv")]}.mp4'
    return None


# Reading a link's URL.

YOUTUBE_ID = re.compile(r'[A-Za-z0-9_-]{11}')
DIGITS = re.compile(r'[0-9]{1,20}')
NAME = re.compile(r'[A-Za-z0-9_-]{1,100}')
# 1h2m3s, 2m, 90s, or 90: how YouTube, Vimeo and Twitch write a start time.
START = re.compile(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?')
HANDLE = re.compile(r'([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]')
DID = re.compile(r'did:(plc|web):[A-Za-z0-9._%:-]{1,200}')
RECORD_KEY = re.compile(r'[A-Za-z0-9._~:-]{1,512}')
# First path parts on Twitch and SoundCloud that are the site's own pages, not a channel or
# an artist.
TWITCH_PAGES = frozenset({
    'directory', 'downloads', 'drops', 'friends', 'inventory', 'jobs', 'messages', 'p',
    'popout', 'prime', 'search', 'settings', 'store', 'subscriptions', 'team', 'turbo',
    'videos', 'wallet',
})  # fmt: skip
SOUNDCLOUD_PAGES = frozenset({
    'charts', 'discover', 'feed', 'jobs', 'messages', 'notifications', 'pages', 'people',
    'pro', 'search', 'settings', 'stream', 'terms-of-use', 'upload', 'you',
})  # fmt: skip
SOUNDCLOUD_TABS = frozenset({
    'albums', 'comments', 'followers', 'following', 'likes', 'popular-tracks', 'reposts',
    'sets', 'tracks',
})  # fmt: skip


def _start(value: str | None) -> int:
    if not value:
        return 0
    found = START.fullmatch(value.strip())
    if found is None:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return hours * 3600 + minutes * 60 + seconds


def _query(parts: SplitResult, name: str) -> str | None:
    values = parse_qs(parts.query).get(name)
    return values[0] if values else None


def _segments(parts: SplitResult) -> list[str]:
    return [segment for segment in parts.path.split('/') if segment]


def _youtube_link(parts: SplitResult, host: str) -> Embed | None:
    path = _segments(parts)
    if host == 'youtu.be':
        video = path[0] if path else None
    elif path[:1] == ['watch']:
        video = _query(parts, 'v')
    elif len(path) >= 2 and path[0] in {'shorts', 'embed', 'live', 'v'}:  # noqa: PLR2004
        video = path[1]
    else:
        return None
    if video is None or not YOUTUBE_ID.fullmatch(video):
        return None
    start = _query(parts, 't') or _query(parts, 'start')
    return Embed('youtube', video, _start(start))


def _vimeo_link(parts: SplitResult, host: str) -> Embed | None:
    path = _segments(parts)
    if host == 'player.vimeo.com':
        if len(path) < 2 or path[0] != 'video':  # noqa: PLR2004
            return None
        video, unlisted = path[1], _query(parts, 'h') or ''
    else:
        # vimeo.com/ID, vimeo.com/ID/HASH, and the same under channels/x/ or groups/x/videos/.
        numbered = [i for i, segment in enumerate(path) if DIGITS.fullmatch(segment)]
        if not numbered:
            return None
        video = path[numbered[0]]
        after = path[numbered[0] + 1 : numbered[0] + 2]
        unlisted = after[0] if after and re.fullmatch(r'[0-9a-f]{6,20}', after[0]) else ''
    if not DIGITS.fullmatch(video) or (unlisted and not NAME.fullmatch(unlisted)):
        return None
    start = _start(parts.fragment.removeprefix('t=')) if parts.fragment.startswith('t=') else 0
    return Embed('vimeo', f'{video}:{unlisted}' if unlisted else video, start)


def _streamable_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    if len(path) == 2 and path[0] in {'e', 'o', 's'}:  # noqa: PLR2004
        path = path[1:]
    return Embed('streamable', path[0]) if len(path) == 1 and NAME.fullmatch(path[0]) else None


def _twitch_link(parts: SplitResult, host: str) -> Embed | None:
    path = _segments(parts)
    if host == 'clips.twitch.tv':
        slug = path[0] if len(path) == 1 else None
        return Embed('twitch', f'clip:{slug}') if slug and NAME.fullmatch(slug) else None
    if len(path) == 2 and path[0] == 'videos' and DIGITS.fullmatch(path[1]):  # noqa: PLR2004
        return Embed('twitch', f'video:{path[1]}', _start(_query(parts, 't')))
    if len(path) == 3 and path[1] == 'clip' and NAME.fullmatch(path[2]):  # noqa: PLR2004
        return Embed('twitch', f'clip:{path[2]}')
    if len(path) == 1 and path[0].lower() not in TWITCH_PAGES and NAME.fullmatch(path[0]):
        return Embed('twitch', f'channel:{path[0].lower()}')
    return None


def _redgifs_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    if len(path) == 2 and path[0] in {'watch', 'ifr'} and path[1].isalpha():  # noqa: PLR2004
        return Embed('redgifs', path[1].lower())
    return None


def _tiktok_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    video = None
    if len(path) == 3:  # noqa: PLR2004
        # /@user/video/ID, and the addresses of TikTok's own players.
        posted = path[0].startswith('@') and path[1] == 'video'
        if posted or path[:2] in (['embed', 'v2'], ['player', 'v1']):
            video = path[2]
    elif len(path) == 2 and path[0] == 'v':  # noqa: PLR2004
        video = path[1].removesuffix('.html')
    return Embed('tiktok', video) if video and DIGITS.fullmatch(video) else None


def _soundcloud_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    if not path or path[0].lower() in SOUNDCLOUD_PAGES or not NAME.fullmatch(path[0]):
        return None
    if len(path) == 2 and path[1] not in SOUNDCLOUD_TABS and NAME.fullmatch(path[1]):  # noqa: PLR2004
        return Embed('soundcloud', f'{path[0]}/{path[1]}')
    if len(path) == 3 and path[1] == 'sets' and NAME.fullmatch(path[2]):  # noqa: PLR2004
        return Embed('soundcloud', f'{path[0]}/sets/{path[2]}')
    return None


def _tenor_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    if len(path) >= 2 and path[-2] == 'view':  # noqa: PLR2004
        gif = path[-1].rsplit('-', 1)[-1]
    elif len(path) == 2 and path[0] == 'embed':  # noqa: PLR2004
        gif = path[1]
    else:
        return None
    return Embed('tenor', gif) if DIGITS.fullmatch(gif) else None


def _giphy_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    if len(path) == 2 and path[0] in {'gifs', 'embed', 'clips'}:  # noqa: PLR2004
        gif = path[1].rsplit('-', 1)[-1]
        return Embed('giphy', gif) if re.fullmatch(r'[A-Za-z0-9]{6,40}', gif) else None
    return None


def _twitter_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    # /user/status/ID, its photo and video subpages, and /i/web/status/ID.
    for i, segment in enumerate(path[:-1]):
        if segment in {'status', 'statuses'} and DIGITS.fullmatch(path[i + 1]):
            return Embed('twitter', path[i + 1])
    return None


def _bluesky_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    if len(path) != 4 or path[0] != 'profile' or path[2] != 'post':  # noqa: PLR2004
        return None
    actor, rkey = path[1].lower(), path[3]
    if not (DID.fullmatch(actor) or HANDLE.fullmatch(actor)) or not RECORD_KEY.fullmatch(rkey):
        return None
    # A handle is swapped for its DID before this is stored; see find().
    return Embed('bluesky', f'{actor}/{rkey}')


def _instagram_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    # /p/CODE, /reel/CODE, /tv/CODE, and the same after a user name.
    for i, segment in enumerate(path[:-1]):
        if segment in {'p', 'reel', 'reels', 'tv'} and NAME.fullmatch(path[i + 1]):
            return Embed('instagram', path[i + 1])
    return None


def _threads_link(parts: SplitResult, _: str) -> Embed | None:
    path = _segments(parts)
    if len(path) >= 3 and path[0].startswith('@') and path[1] == 'post':  # noqa: PLR2004
        user, code = path[0][1:], path[2]
        if re.fullmatch(r'[A-Za-z0-9._]{1,30}', user) and NAME.fullmatch(code):
            return Embed('threads', f'{user}/{code}')
    return None


READERS: dict[str, Callable[[SplitResult, str], Embed | None]] = {
    'youtube.com': _youtube_link,
    'music.youtube.com': _youtube_link,
    'youtube-nocookie.com': _youtube_link,
    'youtu.be': _youtube_link,
    'vimeo.com': _vimeo_link,
    'player.vimeo.com': _vimeo_link,
    'streamable.com': _streamable_link,
    'twitch.tv': _twitch_link,
    'clips.twitch.tv': _twitch_link,
    'redgifs.com': _redgifs_link,
    'v3.redgifs.com': _redgifs_link,
    'tiktok.com': _tiktok_link,
    'soundcloud.com': _soundcloud_link,
    'tenor.com': _tenor_link,
    'giphy.com': _giphy_link,
    'twitter.com': _twitter_link,
    'mobile.twitter.com': _twitter_link,
    'x.com': _twitter_link,
    'bsky.app': _bluesky_link,
    'instagram.com': _instagram_link,
    'threads.net': _threads_link,
    'threads.com': _threads_link,
}


def _host(parts: SplitResult) -> str:
    host = (parts.hostname or '').lower()
    for prefix in ('www.', 'm.'):
        host = host.removeprefix(prefix)
    return host


def match(url: str) -> Embed | None:
    """The embed a URL names, read from the URL alone; None if it is on no listed site."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in {'http', 'https'}:
        return None
    host = _host(parts)
    reader = READERS.get(host)
    return reader(parts, host) if reader else None


def _short(url: str) -> bool:
    parts = urlsplit(url)
    host = _host(parts)
    return host in SHORT_HOSTS or (host == 'tiktok.com' and parts.path.startswith('/t/'))


async def _did(fetcher: Fetcher, handle: str, appview: str) -> str | None:
    query = urlencode({'handle': handle})
    address = f'{appview}/xrpc/com.atproto.identity.resolveHandle?{query}'
    try:
        reply = await fetcher.get(address, limit=MAX_HANDLE_REPLY)
        did = json.loads(reply.body).get('did')
    except (FetchError, ValueError, AttributeError):
        return None
    return did if isinstance(did, str) and DID.fullmatch(did) else None


async def find(fetcher: Fetcher, url: str, appview: str = BLUESKY_APPVIEW) -> Embed | None:
    """The embed for a link, following short links and looking up Bluesky handles."""
    try:
        if _short(url):
            url = await fetcher.locate(url)
    except (FetchError, ValueError):
        return None
    embed = match(url)
    if embed is None or embed.provider != 'bluesky' or embed.id.startswith('did:'):
        return embed
    handle, _sep, rkey = embed.id.partition('/')
    did = await _did(fetcher, handle, appview)
    return Embed('bluesky', f'{did}/{rkey}') if did else None
