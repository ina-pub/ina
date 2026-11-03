"""Community stylesheets and the images they use, as r2 kept them.

The stylesheet's source is the ``config/stylesheet`` wiki page, so it has revisions; what is
served is the source after ``app.text.stylesheet`` has checked and compiled it. Images go to
object storage under a name made from their content and are never deleted from there, so a past
revision's images still load.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import msgspec
from PIL import Image, UnidentifiedImageError

from app.errors import ErrorCode, FieldError, InaError
from app.repos.base import CommunityImage, TooManyImagesError
from app.services.perms import Action, can
from app.text.stylesheet import sanitize

if TYPE_CHECKING:
    from app.infra.storage import ObjectStore
    from app.repos.base import Account, Community, CommunityRepo, ModerationRepo, StylesheetRepo
    from app.services.perms import Standing

MAX_IMAGES = 50
MAX_IMAGE_BYTES = 500 * 1024
# r2 showed this many of a stylesheet's errors at once.
MAX_ERRORS = 20
# Image formats Pillow names, as the extension and type they are stored with.
FORMATS = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg')}
IMAGE_NAME = re.compile(r'\A[a-zA-Z0-9\-]{1,100}\Z')
FORBIDDEN = 403


def media_url(base: str, key: str) -> str:
    return f'{base.rstrip("/")}/{key}'


@dataclass(frozen=True, slots=True)
class Stylesheets:
    """What saving the stylesheet needs: its images, where they are served, and where it goes."""

    repo: StylesheetRepo
    media: str

    async def compile(self, community_id: int, source: str) -> str:
        """The stylesheet to serve; SPECIAL_ERRORS for each problem, by line, if there are any."""
        images = {
            image.name: media_url(self.media, image.key)
            for image in await self.repo.images(community_id)
        }
        css, errors = sanitize(source, images)
        if errors:
            raise InaError(
                *(
                    FieldError(ErrorCode.SPECIAL_ERRORS, 'content', {'error': str(error)})
                    for error in errors[:MAX_ERRORS]
                )
            )
        return css

    async def store(self, community_id: int, css: str) -> None:
        version = hashlib.sha256(css.encode()).hexdigest()[:16] if css else ''
        await self.repo.set_stylesheet(community_id, css, version)


def require_config(actor: Account, community: Community, standing: Standing) -> None:
    if not can(actor, Action.CONFIGURE, community, standing=standing):
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)


@dataclass(frozen=True, slots=True)
class Checked:
    """An image that passed: its bytes, and how to store and size it."""

    data: bytes
    extension: str
    content_type: str
    width: int
    height: int

    @property
    def key_name(self) -> str:
        return f'{hashlib.sha256(self.data).hexdigest()[:24]}.{self.extension}'


def check_image(data: bytes) -> Checked:
    """r2 took PNG and JPEG under 500 KiB; anything Pillow cannot read is not an image."""
    if len(data) > MAX_IMAGE_BYTES:
        raise InaError.of(ErrorCode.IMAGE_ERROR, 'file', reason='too big. keep it under 500 KiB')
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            kind, size = image.format, image.size
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise InaError.of(ErrorCode.IMAGE_ERROR, 'file', reason='invalid image') from None
    if kind not in FORMATS:
        raise InaError.of(ErrorCode.IMAGE_ERROR, 'file', reason='use a png or jpeg image')
    extension, content_type = FORMATS[kind]
    return Checked(data, extension, content_type, size[0], size[1])


async def upload_image(
    repo: StylesheetRepo,
    store: ObjectStore,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    *,
    name: str,
    data: bytes,
) -> str:
    """Store an image for ``url(%%name%%)``, replacing any of that name; returns its key."""
    require_config(actor, community, standing)
    if not IMAGE_NAME.match(name):
        raise InaError.of(ErrorCode.BAD_CSS_NAME, 'name')
    checked = check_image(data)
    key = f'community/{community.id}/{checked.key_name}'
    image = CommunityImage(
        name=name, key=key, width=checked.width, height=checked.height, bytes=len(data)
    )
    try:
        await repo.add_image(community.id, image, limit=MAX_IMAGES)
    except TooManyImagesError:
        reason = f'a community can keep {MAX_IMAGES} images; delete one first'
        raise InaError.of(ErrorCode.IMAGE_ERROR, 'file', reason=reason) from None
    await store.put(key, data, checked.content_type)
    await moderation.log(community.id, actor.id, 'editsettings', details='upload_image')
    return key


async def delete_image(
    repo: StylesheetRepo,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    name: str,
) -> None:
    require_config(actor, community, standing)
    if await repo.delete_image(community.id, name):
        await moderation.log(community.id, actor.id, 'editsettings', details='del_image')


async def upload_header(
    communities: CommunityRepo,
    store: ObjectStore,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    *,
    data: bytes,
    title: str = '',
) -> str:
    """The image in the header in place of ina's name, as r2's header image."""
    require_config(actor, community, standing)
    checked = check_image(data)
    key = f'community/{community.id}/{checked.key_name}'
    await store.put(key, data, checked.content_type)
    settings = msgspec.structs.replace(
        community.settings,
        header_img=key,
        header_size=(checked.width, checked.height),
        header_title=title.strip()[:500],
    )
    await communities.set_settings(community.id, settings)
    await moderation.log(community.id, actor.id, 'editsettings', details='upload_image')
    return key


async def delete_header(
    communities: CommunityRepo,
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
) -> None:
    require_config(actor, community, standing)
    settings = msgspec.structs.replace(
        community.settings, header_img='', header_size=None, header_title=''
    )
    await communities.set_settings(community.id, settings)
    await moderation.log(community.id, actor.id, 'editsettings', details='del_header')
