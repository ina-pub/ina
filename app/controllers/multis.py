"""Multis: their listings, r2's /api/multi, and the forms on a multi's own page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from litestar import Request, delete, get, post, put
from litestar.exceptions import NotFoundException
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.response import Redirect
from litestar.status_codes import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.controllers.auth import local_path, to_viewer
from app.controllers.deps import (
    AccountsDep,
    CacheDep,
    CommunitiesDep,
    DetailsDep,
    LinksDep,
    MultisDep,
    UserDep,
    ViewerDep,
    VotesDep,
)
from app.controllers.listings import build_listing, over_18, page_request
from app.controllers.render import Rendered
from app.errors import ErrorCode, InaError
from app.repos.base import CommunityRepo, Multi, MultiRepo, Sort
from app.services import multis as service
from app.services.listings import Scope
from app.services.sessions import Authenticated
from app.views.forms import FormResult
from app.views.multis import MultiJson, MultiList, MultiPage, Plain

SORTS = {'': Sort.HOT, **{sort.value: sort for sort in Sort}}


@dataclass
class ModelForm:
    """r2's ``model`` JSON, or the same fields from a plain form."""

    model: str = ''
    name: str = ''
    display_name: str = ''
    description_md: str = ''
    visibility: str = ''
    # A plain form's list of community names, separated by commas or spaces.
    communities: str = ''


@dataclass
class EditForm(ModelForm):
    # add, remove, update, delete or copy.
    action: str = ''
    community_name: str = ''
    dest: str = ''


@dataclass
class CopyForm:
    to: str = ''
    display_name: str = ''


def model_of(form: ModelForm) -> service.Model:
    if form.model.strip():
        return service.parse_model(form.model)
    names = form.communities.replace(',', ' ').split()
    return service.Model(
        display_name=form.display_name or None,
        description_md=form.description_md or None,
        visibility=form.visibility or None,
        communities=tuple(names) if names else None,
    )


async def visible_ids(
    communities: CommunityRepo, viewer: Authenticated | None, ids: list[int]
) -> list[int]:
    """Leave out private communities the viewer is not part of, as r2's multis did."""
    hidden = set(
        await communities.excluded_from_search(
            viewer.account.id if viewer else None, include_nsfw=True
        )
    )
    return [community_id for community_id in ids if community_id not in hidden]


@get(
    ['/user/{username:str}/m/{multiname:str}', '/user/{username:str}/m/{multiname:str}/{sort:str}']
)
async def show_multi(
    username: FromPath[str],
    multiname: FromPath[str],
    links: LinksDep,
    votes: VotesDep,
    communities: CommunitiesDep,
    details: DetailsDep,
    multis: MultisDep,
    cache: CacheDep,
    viewer: ViewerDep,
    sort: FromPath[str] = '',
    after: FromQuery[str] = '',
    before: FromQuery[str] = '',
    count: FromQuery[int] = 0,
    limit: FromQuery[int | None] = None,
    t: FromQuery[str] = '',
) -> MultiPage:
    if sort not in SORTS:
        raise NotFoundException
    account = viewer.account if viewer else None
    try:
        multi = await service.viewable(multis, username, multiname, account)
    except InaError as error:
        raise NotFoundException from error
    ids = await visible_ids(
        communities, viewer, [community_id for community_id, _ in multi.communities]
    )
    listing = await build_listing(
        Scope(tuple(ids), None),
        SORTS[sort],
        page_request(after, before, count, limit, t),
        links=links,
        votes=votes,
        cache=cache,
        viewer=viewer,
        base_path=f'{multi.path}/',
        include_nsfw=over_18(viewer),
        details=details,
    )
    shown = set(ids)
    return MultiPage(
        viewer=to_viewer(viewer),
        listing=listing,
        multi=multi,
        names=tuple(name for community_id, name in multi.communities if community_id in shown),
        can_edit=service.may_edit(multi, account),
    )


@get('/me/m/{multiname:str}')
async def show_my_multi(multiname: FromPath[str], user: UserDep) -> Redirect:
    return Redirect(f'/user/{user.account.name}/m/{multiname}')


@post('/user/{username:str}/m/{multiname:str}/edit')
async def edit_multi(
    username: FromPath[str],
    multiname: FromPath[str],
    data: URLEncodedBody[EditForm],
    communities: CommunitiesDep,
    multis: MultisDep,
    user: UserDep,
) -> Rendered:
    """The forms on a multi's page, since a plain form can only POST."""
    multi = await service.viewable(multis, username, multiname, user.account)
    dest = local_path(data.dest, multi.path)
    try:
        if data.action == 'add':
            await service.add_community(
                multis, communities, user.account, multi, data.community_name
            )
        elif data.action == 'remove':
            await service.remove_community(
                multis, communities, user.account, multi, data.community_name
            )
        elif data.action == 'update':
            await service.update(multis, communities, user.account, multi, model_of(data))
        elif data.action == 'delete':
            await service.delete(multis, user.account, multi)
            dest = f'/user/{user.account.name}'
        elif data.action == 'copy':
            copied = await service.copy(
                multis, communities, user.account, multi, data.name, data.display_name
            )
            dest = copied.path
        else:
            raise InaError.of(ErrorCode.INVALID_OPTION, 'action')
    except InaError as error:
        if error.status_code in {HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND}:
            raise
        return Rendered(content=FormResult(errors=error.errors, redirect=multi.path))
    return Rendered(content=FormResult(redirect=dest))


@get('/api/multi/mine')
async def my_multis(multis: MultisDep, user: UserDep) -> MultiList:
    return MultiList(multis=tuple(await multis.by_owner(user.account.id)), can_edit=True)


@get('/api/multi/user/{username:str}')
async def user_multis(
    username: FromPath[str], accounts: AccountsDep, multis: MultisDep, viewer: ViewerDep
) -> MultiList:
    """A user's public multis; their owner sees them all."""
    owner = await accounts.by_name(username)
    if owner is None:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, status_code=HTTP_404_NOT_FOUND)
    own = viewer is not None and viewer.account.id == owner.id
    found = await multis.by_owner(owner.id)
    return MultiList(
        multis=tuple(m for m in found if own or m.visibility == 'public'), can_edit=own
    )


MULTI = '/api/multi/user/{username:str}/m/{multiname:str}'


@get(MULTI)
async def get_multi(
    username: FromPath[str], multiname: FromPath[str], multis: MultisDep, viewer: ViewerDep
) -> MultiJson:
    account = viewer.account if viewer else None
    multi = await service.viewable(multis, username, multiname, account)
    return MultiJson(multi=multi, can_edit=service.may_edit(multi, account))


def require_own_path(username: str, user: Authenticated) -> None:
    if username.lower() != user.account.name.lower():
        raise InaError.of(ErrorCode.MULTI_CANNOT_EDIT, 'multipath', status_code=HTTP_403_FORBIDDEN)


@post(MULTI)
async def create_multi(
    username: FromPath[str],
    multiname: FromPath[str],
    data: URLEncodedBody[ModelForm],
    communities: CommunitiesDep,
    multis: MultisDep,
    user: UserDep,
) -> MultiJson:
    """Create a multi; MULTI_EXISTS if there is one, as r2's POST."""
    require_own_path(username, user)
    multi = await service.create(multis, communities, user.account, multiname, model_of(data))
    return MultiJson(multi=multi, can_edit=True, created=True)


@put(MULTI)
async def put_multi(
    username: FromPath[str],
    multiname: FromPath[str],
    data: URLEncodedBody[ModelForm],
    communities: CommunitiesDep,
    multis: MultisDep,
    user: UserDep,
) -> MultiJson:
    """Create or update a multi, as r2's PUT."""
    require_own_path(username, user)
    found = await multis.by_path(username, multiname)
    if found is None:
        multi = await service.create(multis, communities, user.account, multiname, model_of(data))
        return MultiJson(multi=multi, can_edit=True, created=True)
    multi = await service.update(multis, communities, user.account, found, model_of(data))
    return MultiJson(multi=multi, can_edit=True)


@delete(MULTI, status_code=200)
async def delete_multi(
    username: FromPath[str], multiname: FromPath[str], multis: MultisDep, user: UserDep
) -> Plain:
    multi = await service.viewable(multis, username, multiname, user.account)
    await service.delete(multis, user.account, multi)
    return Plain()


@post('/api/multi')
async def create_multi_from_form(
    data: URLEncodedBody[EditForm], communities: CommunitiesDep, multis: MultisDep, user: UserDep
) -> Rendered:
    """A new multi of one's own, named by ``name`` or made from ``display_name``."""
    name = data.name.strip() or service.slugify(data.display_name)
    multi = await service.create(multis, communities, user.account, name, model_of(data))
    result = FormResult(redirect=multi.path, data={'path': multi.path})
    return Rendered(content=result)


@post('/api/multi/copy')
async def copy_multi(
    request: Request[Any, Any, Any],
    data: URLEncodedBody[CopyForm],
    communities: CommunitiesDep,
    multis: MultisDep,
    user: UserDep,
) -> MultiJson:
    """r2's copy: ``from`` a multi path ``to`` one of one's own."""
    source_path = str((await request.form()).get('from', ''))
    source = await multi_at(multis, source_path, user)
    target = parse_path(data.to)
    require_own_path(target[0], user)
    copied = await service.copy(
        multis, communities, user.account, source, target[1], data.display_name
    )
    return MultiJson(multi=copied, can_edit=True, created=True)


def parse_path(path: str) -> tuple[str, str]:
    """``/user/<owner>/m/<name>`` as (owner, name)."""
    parts = path.strip('/').split('/')
    if len(parts) != 4 or parts[0] != 'user' or parts[2] != 'm':  # noqa: PLR2004 - four parts
        raise InaError.of(ErrorCode.BAD_MULTI_NAME, 'multipath')
    return parts[1], service.check_name(parts[3])


async def multi_at(multis: MultiRepo, path: str, user: Authenticated) -> Multi:
    owner, name = parse_path(path)
    return await service.viewable(multis, owner, name, user.account)


@get(MULTI + '/in/{cname:str}')
async def get_multi_community(
    username: FromPath[str],
    multiname: FromPath[str],
    cname: FromPath[str],
    multis: MultisDep,
    viewer: ViewerDep,
) -> Plain:
    multi = await service.viewable(multis, username, multiname, viewer.account if viewer else None)
    found = [name for _, name in multi.communities if name.lower() == cname.lower()]
    if not found:
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'cname', status_code=HTTP_404_NOT_FOUND)
    return Plain(data={'name': found[0]})


@put(MULTI + '/in/{cname:str}')
async def add_multi_community(
    username: FromPath[str],
    multiname: FromPath[str],
    cname: FromPath[str],
    communities: CommunitiesDep,
    multis: MultisDep,
    user: UserDep,
) -> Plain:
    multi = await service.viewable(multis, username, multiname, user.account)
    await service.add_community(multis, communities, user.account, multi, cname)
    return Plain(data={'name': cname})


@delete(MULTI + '/in/{cname:str}', status_code=200)
async def remove_multi_community(
    username: FromPath[str],
    multiname: FromPath[str],
    cname: FromPath[str],
    communities: CommunitiesDep,
    multis: MultisDep,
    user: UserDep,
) -> Plain:
    multi = await service.viewable(multis, username, multiname, user.account)
    await service.remove_community(multis, communities, user.account, multi, cname)
    return Plain()
