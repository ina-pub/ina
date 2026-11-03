"""Community stylesheets and images: the editor, r2's API for them, and serving both."""

from __future__ import annotations

import re
from dataclasses import dataclass

from litestar import Response, get, post
from litestar.datastructures import UploadFile
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery, MultipartBody, URLEncodedBody
from litestar.status_codes import HTTP_403_FORBIDDEN

from app.controllers.assets import IMMUTABLE
from app.controllers.auth import to_viewer
from app.controllers.communities import sidebar, visible
from app.controllers.deps import (
    CommunitiesDep,
    ModerationDep,
    SettingsDep,
    StoreDep,
    StylesheetsDep,
    SubscriptionsDep,
    UserDep,
    ViewerDep,
    WikiDep,
)
from app.controllers.render import Rendered
from app.errors import FieldError, InaError
from app.infra.storage import StorageError
from app.repos.base import Community, CommunityRepo, StylesheetRepo, SubscriptionRepo, WikiRepo
from app.services import stylesheets as service
from app.services import wiki as wiki_service
from app.services.perms import Standing
from app.services.sessions import Authenticated
from app.views.forms import FormResult
from app.views.stylesheets import ImageRow, StylesheetPage

# A served stylesheet without the right version may change; check back soon.
SHORT_CACHE = 'public, max-age=60'
MEDIA_KEY = re.compile(r'\A(community|thumbs)/[a-z0-9/]+\.(png|jpg)\Z')


@dataclass
class StylesheetForm:
    stylesheet_contents: str = ''
    reason: str = ''
    # r2's op: save, or preview, which only checks.
    op: str = 'save'


@dataclass
class UploadForm:
    file: UploadFile | None = None
    name: str = ''
    # img: one for url(%%name%%); header: the header image.
    upload_type: str = 'img'
    header_title: str = ''


@dataclass
class ImageForm:
    img_name: str = ''


def editor_url(community: Community) -> str:
    return f'{community.path}about/stylesheet'


async def editor(
    community: Community,
    standing: Standing,
    *,
    viewer: Authenticated,
    media: str,
    wiki: WikiRepo,
    repo: StylesheetRepo,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    source: str | None = None,
    errors: tuple[FieldError, ...] = (),
    saved: bool = False,
) -> StylesheetPage:
    service.require_config(viewer.account, community, standing)
    if source is None:
        page = await wiki.page(community.id, wiki_service.STYLESHEET)
        source = page.content_md if page else ''
    header = community.settings.header_img
    return StylesheetPage(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=await sidebar(communities, subscriptions, viewer, community, standing),
        source=source,
        images=tuple(
            ImageRow(image, service.media_url(media, image.key))
            for image in await repo.images(community.id)
        ),
        header_url=service.media_url(media, header) if header else None,
        errors=errors,
        saved=saved,
    )


@get('/in/{name:str}/about/stylesheet')
async def show_stylesheet(
    name: FromPath[str],
    settings: SettingsDep,
    wiki: WikiDep,
    stylesheets: StylesheetsDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
    saved: FromQuery[str] = '',
) -> StylesheetPage:
    community, standing = await visible(communities, user, name)
    return await editor(
        community,
        standing,
        viewer=user,
        media=settings.media_url,
        wiki=wiki,
        repo=stylesheets,
        communities=communities,
        subscriptions=subscriptions,
        saved=saved == 'true',
    )


@post('/in/{name:str}/api/community_stylesheet')
async def save_stylesheet(
    name: FromPath[str],
    data: URLEncodedBody[StylesheetForm],
    settings: SettingsDep,
    wiki: WikiDep,
    stylesheets: StylesheetsDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
) -> Rendered:
    """Save the stylesheet as the config/stylesheet wiki page, if it compiles."""
    community, standing = await visible(communities, user, name)
    service.require_config(user.account, community, standing)
    compiler = service.Stylesheets(stylesheets, settings.media_url)
    try:
        if data.op == 'preview':
            await compiler.compile(community.id, data.stylesheet_contents)
        else:
            who = await wiki_service.access(moderation, user.account, standing, community)
            repos = wiki_service.Repos(
                wiki=wiki, communities=communities, moderation=moderation, stylesheets=compiler
            )
            await wiki_service.edit(
                repos,
                user.account,
                who,
                community,
                wiki_service.STYLESHEET,
                data.stylesheet_contents,
                reason=data.reason,
                check_enabled=False,
            )
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        page = await editor(
            community,
            standing,
            viewer=user,
            media=settings.media_url,
            wiki=wiki,
            repo=stylesheets,
            communities=communities,
            subscriptions=subscriptions,
            source=data.stylesheet_contents,
            errors=error.errors,
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    return Rendered(content=FormResult(redirect=f'{editor_url(community)}?saved=true'))


@post('/in/{name:str}/api/upload_community_img')
async def upload_image(
    name: FromPath[str],
    data: MultipartBody[UploadForm],
    settings: SettingsDep,
    wiki: WikiDep,
    stylesheets: StylesheetsDep,
    store: StoreDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
) -> Rendered:
    """An image for the stylesheet (``name``), or the header (``upload_type=header``)."""
    community, standing = await visible(communities, user, name)
    content = await data.file.read() if data.file is not None else b''
    try:
        if data.upload_type == 'header':
            key = await service.upload_header(
                communities,
                store,
                moderation,
                user.account,
                standing,
                community,
                data=content,
                title=data.header_title,
            )
        else:
            key = await service.upload_image(
                stylesheets,
                store,
                moderation,
                user.account,
                standing,
                community,
                name=data.name.strip(),
                data=content,
            )
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        page = await editor(
            community,
            standing,
            viewer=user,
            media=settings.media_url,
            wiki=wiki,
            repo=stylesheets,
            communities=communities,
            subscriptions=subscriptions,
            errors=error.errors,
        )
        return Rendered(content=FormResult(errors=error.errors, page=page))
    result = FormResult(
        redirect=editor_url(community), data={'img_src': service.media_url(settings.media_url, key)}
    )
    return Rendered(content=result)


@post('/in/{name:str}/api/delete_community_img')
async def delete_image(
    name: FromPath[str],
    data: URLEncodedBody[ImageForm],
    stylesheets: StylesheetsDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    user: UserDep,
) -> Rendered:
    community, standing = await visible(communities, user, name)
    await service.delete_image(
        stylesheets, moderation, user.account, standing, community, data.img_name
    )
    return Rendered(content=FormResult(redirect=editor_url(community)))


@post('/in/{name:str}/api/delete_community_header')
async def delete_header(
    name: FromPath[str], moderation: ModerationDep, communities: CommunitiesDep, user: UserDep
) -> Rendered:
    community, standing = await visible(communities, user, name)
    await service.delete_header(communities, moderation, user.account, standing, community)
    return Rendered(content=FormResult(redirect=editor_url(community)))


@get('/in/{name:str}/stylesheet.css')
async def serve_stylesheet(
    name: FromPath[str],
    stylesheets: StylesheetsDep,
    communities: CommunitiesDep,
    viewer: ViewerDep,
    v: FromQuery[str] = '',
) -> Response[str]:
    """The compiled stylesheet; cached for a year when asked for by its current version."""
    community, _ = await visible(communities, viewer, name)
    css = await stylesheets.stylesheet(community.id)
    if css is None:
        raise NotFoundException
    current = bool(v) and v == community.stylesheet_hash
    return Response(
        content=css,
        media_type='text/css; charset=utf-8',
        headers={'Cache-Control': IMMUTABLE if current else SHORT_CACHE},
    )


@get('/media/{key:path}')
async def serve_media(key: FromPath[str], store: StoreDep) -> Response[bytes]:
    """Uploaded images and thumbnails; named after their content, so they never change."""
    key = key.lstrip('/')
    if not MEDIA_KEY.match(key):
        raise NotFoundException
    try:
        found = await store.get(key)
    except StorageError as error:
        raise NotFoundException from error
    if found is None:
        raise NotFoundException
    return Response(
        content=found.body, media_type=found.content_type, headers={'Cache-Control': IMMUTABLE}
    )
