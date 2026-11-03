"""Multis, as r2 had them: a user's named set of communities, read as one listing.

Only the owner changes one. A private multi is the owner's alone; a public one is listed on the
owner's profile; a hidden one can be read by anyone with its address. Anyone who can read one
may copy it, as a new private multi of their own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.errors import ErrorCode, InaError
from app.repos.base import MultiExistsError, MultiFields
from app.text import markdown

if TYPE_CHECKING:
    from app.repos.base import Account, CommunityRepo, Multi, MultiRepo

NAME = re.compile(r'\A[A-Za-z0-9][A-Za-z0-9_]{1,20}\Z')
COLOR = re.compile(r'\A#[0-9a-fA-F]{6}\Z')
MAX_DISPLAY_NAME = 50
MAX_DESCRIPTION = 10000
MAX_COMMUNITIES = 100
VISIBILITIES = ('private', 'public', 'hidden')
WEIGHTINGS = ('classic', 'fresh')
FORBIDDEN = 403
NOT_FOUND = 404
CONFLICT = 409


@dataclass(frozen=True, slots=True, kw_only=True)
class Model:
    """r2's multi ``model``: what to change; None leaves a field as it is."""

    display_name: str | None = None
    description_md: str | None = None
    visibility: str | None = None
    key_color: str | None = None
    weighting_scheme: str | None = None
    communities: tuple[str, ...] | None = None


def parse_model(raw: str) -> Model:
    """The JSON r2's API sent as ``model``."""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise InaError.of(ErrorCode.JSON_PARSE_ERROR, 'model') from None
    if not isinstance(data, dict):
        raise InaError.of(ErrorCode.JSON_PARSE_ERROR, 'model')
    names = None
    if 'communities' in data:
        entries = data['communities']
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) and isinstance(entry.get('name'), str) for entry in entries
        ):
            raise InaError.of(ErrorCode.JSON_PARSE_ERROR, 'model')
        names = tuple(entry['name'] for entry in entries)

    def text(key: str) -> str | None:
        value = data.get(key)
        return str(value) if value is not None else None

    return Model(
        display_name=text('display_name'),
        description_md=text('description_md'),
        visibility=text('visibility'),
        key_color=text('key_color'),
        weighting_scheme=text('weighting_scheme'),
        communities=names,
    )


def check_name(name: str, field: str = 'multipath') -> str:
    if not NAME.match(name):
        raise InaError.of(ErrorCode.BAD_MULTI_NAME, field)
    return name


def slugify(display_name: str) -> str:
    """A path name from a display name, as r2 made one."""
    slug = re.sub(r'[^A-Za-z0-9_]+', '_', display_name).strip('_')[:21]
    return slug if NAME.match(slug) else check_name('')


def may_view(multi: Multi, account: Account | None) -> bool:
    return multi.visibility != 'private' or (account is not None and account.id == multi.owner_id)


def may_edit(multi: Multi, account: Account | None) -> bool:
    return account is not None and account.id == multi.owner_id


def require_edit(multi: Multi, account: Account) -> None:
    if not may_edit(multi, account):
        raise InaError.of(ErrorCode.MULTI_CANNOT_EDIT, 'multipath', status_code=FORBIDDEN)


async def viewable(repo: MultiRepo, owner: str, name: str, account: Account | None) -> Multi:
    """The multi, unless it is missing or private to someone else; both look the same."""
    found = await repo.by_path(owner, name)
    if found is None or not may_view(found, account):
        raise InaError.of(ErrorCode.MULTI_NOT_FOUND, 'multipath', status_code=NOT_FOUND)
    return found


def _fields(current: MultiFields, model: Model) -> MultiFields:
    display_name = model.display_name if model.display_name is not None else current.display_name
    if len(display_name) > MAX_DISPLAY_NAME:
        raise InaError.of(ErrorCode.TOO_LONG, 'display_name', max_length=MAX_DISPLAY_NAME)
    description = (
        model.description_md if model.description_md is not None else current.description_md
    )
    if len(description) > MAX_DESCRIPTION:
        raise InaError.of(ErrorCode.TOO_LONG, 'description_md', max_length=MAX_DESCRIPTION)
    visibility = model.visibility or current.visibility
    if visibility not in VISIBILITIES:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'visibility')
    weighting = model.weighting_scheme or current.weighting_scheme
    if weighting not in WEIGHTINGS:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'weighting_scheme')
    color = model.key_color if model.key_color is not None else current.key_color
    if color and not COLOR.match(color):
        raise InaError.of(ErrorCode.BAD_COLOR, 'key_color')
    return MultiFields(
        display_name=display_name.strip(),
        description_md=description,
        description_html=markdown.render(description),
        visibility=visibility,
        key_color=color,
        weighting_scheme=weighting,
    )


async def _community_ids(communities: CommunityRepo, names: tuple[str, ...]) -> list[int]:
    stripped = (name.strip().removeprefix('/').removeprefix('in/') for name in names)
    unique = list(dict.fromkeys(name for name in stripped if name))
    if len(unique) > MAX_COMMUNITIES:
        raise InaError.of(ErrorCode.MULTI_TOO_MANY_COMMUNITIES, 'communities', status_code=CONFLICT)
    ids = await communities.ids_by_names(unique)
    if len(ids) != len(unique):
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'communities')
    return ids


EMPTY = MultiFields(
    display_name='',
    description_md='',
    description_html='',
    visibility='private',
    key_color='',
    weighting_scheme='classic',
)


def fields_of(multi: Multi) -> MultiFields:
    return MultiFields(
        display_name=multi.display_name,
        description_md=multi.description_md,
        description_html=multi.description_html,
        visibility=multi.visibility,
        key_color=multi.key_color,
        weighting_scheme=multi.weighting_scheme,
    )


async def create(
    repo: MultiRepo,
    communities: CommunityRepo,
    owner: Account,
    name: str,
    model: Model,
    *,
    copied_from: str | None = None,
) -> Multi:
    """MULTI_EXISTS if the owner already has one of that name, as r2's POST."""
    check_name(name)
    fields = _fields(replace(EMPTY, display_name=name), model)
    ids = await _community_ids(communities, model.communities or ())
    try:
        multi_id = await repo.create(owner.id, name, fields, copied_from=copied_from)
    except MultiExistsError:
        raise InaError.of(ErrorCode.MULTI_EXISTS, 'multipath', status_code=CONFLICT) from None
    await repo.set_communities(multi_id, ids)
    created = await repo.by_path(owner.name, name)
    assert created is not None  # noqa: S101 - just made
    return created


async def update(
    repo: MultiRepo, communities: CommunityRepo, account: Account, multi: Multi, model: Model
) -> Multi:
    require_edit(multi, account)
    await repo.update(multi.id, _fields(fields_of(multi), model))
    if model.communities is not None:
        await repo.set_communities(multi.id, await _community_ids(communities, model.communities))
    changed = await repo.by_path(multi.owner_name, multi.name)
    assert changed is not None  # noqa: S101 - just changed
    return changed


async def add_community(
    repo: MultiRepo, communities: CommunityRepo, account: Account, multi: Multi, name: str
) -> None:
    require_edit(multi, account)
    ids = await communities.ids_by_names([name.strip()])
    if not ids:
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'cname')
    if ids[0] not in {community_id for community_id, _ in multi.communities} and (
        len(multi.communities) >= MAX_COMMUNITIES
    ):
        raise InaError.of(ErrorCode.MULTI_TOO_MANY_COMMUNITIES, 'cname', status_code=CONFLICT)
    await repo.add_community(multi.id, ids[0])


async def remove_community(
    repo: MultiRepo, communities: CommunityRepo, account: Account, multi: Multi, name: str
) -> None:
    require_edit(multi, account)
    ids = await communities.ids_by_names([name.strip()])
    if ids:
        await repo.remove_community(multi.id, ids[0])


async def delete(repo: MultiRepo, account: Account, multi: Multi) -> None:
    require_edit(multi, account)
    await repo.delete(multi.id)


async def copy(
    repo: MultiRepo,
    communities: CommunityRepo,
    account: Account,
    source: Multi,
    name: str,
    display_name: str = '',
) -> Multi:
    """A private copy for ``account``, as r2's /api/multi/copy."""
    model = Model(
        display_name=display_name or source.display_name,
        description_md=source.description_md,
        visibility='private',
        key_color=source.key_color,
        weighting_scheme=source.weighting_scheme,
        communities=tuple(community_name for _, community_name in source.communities),
    )
    return await create(repo, communities, account, name, model, copied_from=source.path)
