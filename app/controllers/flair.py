"""Flair: the moderators' flair page and r2's flair API, under ``/in/{name}/``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from litestar import Request, get, post
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.status_codes import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.controllers.auth import CSRF_EXEMPT, local_path, to_viewer
from app.controllers.communities import sidebar, visible
from app.controllers.deps import (
    AccountsDep,
    CacheDep,
    CommunitiesDep,
    FlairDep,
    LinksDep,
    ModerationDep,
    SubscriptionsDep,
    UserDep,
)
from app.controllers.moderation import refreshed, truthy
from app.controllers.render import Rendered
from app.errors import ErrorCode, FieldError, InaError
from app.ids import Fullname, Kind
from app.repos.base import (
    Account,
    AccountRepo,
    Community,
    CommunityRepo,
    FlairKind,
    FlairRepo,
    FlairTemplate,
    Link,
    LinkRepo,
    SubscriptionRepo,
)
from app.services import flair as service
from app.services.perms import Standing
from app.services.sessions import Authenticated
from app.views.flair import CsvResults, FlairBox, FlairList, FlairPage, FlairSelector
from app.views.forms import FormResult

PAGE_SIZE = 100
MAX_LIST = 1000


@dataclass
class FlairForm:
    name: str = ''
    link: str = ''
    text: str = ''
    css_class: str = ''
    dest: str = ''


@dataclass
class ConfigForm:
    flair_enabled: str = ''
    flair_position: str = 'right'
    link_flair_position: str = ''
    flair_self_assign_enabled: str = ''
    link_flair_self_assign_enabled: str = ''


@dataclass
class TemplateForm:
    flair_template_id: str = ''
    flair_type: str = ''
    text: str = ''
    css_class: str = ''
    text_editable: str = ''


@dataclass
class SelectForm:
    name: str = ''
    link: str = ''
    flair_template_id: str = ''
    text: str = ''
    dest: str = ''


@dataclass
class EnabledForm:
    flair_enabled: str = ''
    dest: str = ''


@dataclass
class CsvForm:
    flair_csv: str = ''


async def flair_page(
    community: Community,
    standing: Standing,
    *,
    flair: FlairRepo,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    viewer: Authenticated,
    after: str = '',
    name: str = '',
    errors: tuple[FieldError, ...] = (),
) -> FlairPage:
    service.require_moderator(standing)
    users = await flair.flair_list(
        community.id, limit=PAGE_SIZE + 1, after=after or None, name=name or None
    )
    return FlairPage(
        viewer=to_viewer(viewer),
        community=community,
        sidebar=await sidebar(communities, subscriptions, viewer, community, standing),
        users=tuple(users[:PAGE_SIZE]),
        after=users[PAGE_SIZE - 1].name if len(users) > PAGE_SIZE else None,
        user_templates=tuple(await flair.templates(community.id, FlairKind.USER)),
        link_templates=tuple(await flair.templates(community.id, FlairKind.LINK)),
        errors=errors,
    )


@get('/in/{name:str}/about/flair')
async def show_flair(
    name: FromPath[str],
    flair: FlairDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
    after: FromQuery[str] = '',
    jump: FromQuery[str] = '',
) -> FlairPage:
    community, standing = await visible(communities, user, name)
    return await flair_page(
        community,
        standing,
        flair=flair,
        communities=communities,
        subscriptions=subscriptions,
        viewer=user,
        after=after,
        name=jump,
    )


async def target_account(accounts: AccountRepo, name: str) -> Account:
    found = await accounts.by_name(name.strip()) if name.strip() else None
    if found is None:
        raise InaError.of(ErrorCode.BAD_FLAIR_TARGET, 'name', status_code=HTTP_404_NOT_FOUND)
    return found


async def target_link(links: LinkRepo, community: Community, fullname: str) -> Link:
    try:
        parsed = Fullname.parse(fullname)
    except ValueError:
        parsed = None
    found = await links.by_id(parsed.id) if parsed and parsed.kind is Kind.LINK else None
    if found is None or found.community_id != community.id:
        raise InaError.of(ErrorCode.BAD_FLAIR_TARGET, 'link', status_code=HTTP_404_NOT_FOUND)
    return found


def flair_url(community: Community) -> str:
    return f'{community.path}about/flair'


async def mod_change(
    community: Community,
    standing: Standing,
    *,
    flair: FlairRepo,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    user: Authenticated,
    error: InaError,
) -> Rendered:
    """An error from one of the flair page's forms: the page again, with it."""
    if error.status_code in {HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND}:
        raise error
    page = await flair_page(
        community,
        standing,
        flair=flair,
        communities=communities,
        subscriptions=subscriptions,
        viewer=user,
        errors=error.errors,
    )
    return Rendered(content=FormResult(errors=error.errors, page=page))


@post('/in/{name:str}/api/flair')
async def set_flair(
    name: FromPath[str],
    data: URLEncodedBody[FlairForm],
    flair: FlairDep,
    accounts: AccountsDep,
    links: LinksDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    """A moderator sets a user's flair (``name``) or a link's (``link``); empty removes it."""
    community, standing = await visible(communities, user, name)
    service.require_moderator(standing)
    try:
        text, css = service.check_text(data.text), service.check_css(data.css_class)
        if data.link:
            link = await target_link(links, community, data.link)
            await service.set_link_flair(
                flair, moderation, user.account, community, link, text=text, css_class=css
            )
            result = FormResult(redirect=local_path(data.dest, flair_url(community)))
            return Rendered(content=result, background=refreshed(cache, link))
        target = await target_account(accounts, data.name)
        await service.set_user_flair(
            flair, moderation, user.account, community, target, text=text, css_class=css
        )
    except InaError as error:
        return await mod_change(
            community,
            standing,
            flair=flair,
            communities=communities,
            subscriptions=subscriptions,
            user=user,
            error=error,
        )
    return Rendered(content=FormResult(redirect=local_path(data.dest, flair_url(community))))


@post('/in/{name:str}/api/deleteflair')
async def delete_flair(
    name: FromPath[str],
    data: URLEncodedBody[FlairForm],
    flair: FlairDep,
    accounts: AccountsDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    user: UserDep,
) -> Rendered:
    community, standing = await visible(communities, user, name)
    service.require_moderator(standing)
    target = await target_account(accounts, data.name)
    await service.set_user_flair(
        flair, moderation, user.account, community, target, text='', css_class=''
    )
    return Rendered(content=FormResult(redirect=flair_url(community)))


@post('/in/{name:str}/api/flaircsv')
async def flair_csv(
    name: FromPath[str],
    data: URLEncodedBody[CsvForm],
    flair: FlairDep,
    accounts: AccountsDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    user: UserDep,
) -> CsvResults:
    community, standing = await visible(communities, user, name)
    lines = await service.apply_csv(
        flair, moderation, accounts, user.account, standing, community, data.flair_csv
    )
    return CsvResults(lines=tuple(lines))


@get('/in/{name:str}/api/flairlist')
async def flair_list(
    request: Request[Any, Any, Any],
    name: FromPath[str],
    flair: FlairDep,
    communities: CommunitiesDep,
    user: UserDep,
    after: FromQuery[str] = '',
    limit: FromQuery[int] = PAGE_SIZE,
) -> FlairList:
    """Users with flair here, by name; ``after`` continues, as r2's ``next``.

    r2's ``name`` picks out one user; the path already uses that name, so it is read directly.
    """
    community, standing = await visible(communities, user, name)
    service.require_moderator(standing)
    size = max(1, min(limit, MAX_LIST))
    one = request.query_params.get('name', '').strip()
    users = await flair.flair_list(
        community.id, limit=size + 1, after=after or None, name=one or None
    )
    shown = users[:size]
    return FlairList(users=tuple(shown), next=shown[-1].name if len(users) > size else None)


@post('/in/{name:str}/api/flairconfig')
async def flair_config(
    name: FromPath[str],
    data: URLEncodedBody[ConfigForm],
    flair: FlairDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
) -> Rendered:
    community, standing = await visible(communities, user, name)
    form = service.ConfigForm(
        flair_enabled=truthy(data.flair_enabled),
        flair_position=data.flair_position,
        link_flair_position=data.link_flair_position,
        flair_self_assign_enabled=truthy(data.flair_self_assign_enabled),
        link_flair_self_assign_enabled=truthy(data.link_flair_self_assign_enabled),
    )
    try:
        await service.configure(communities, moderation, user.account, standing, community, form)
    except InaError as error:
        return await mod_change(
            community,
            standing,
            flair=flair,
            communities=communities,
            subscriptions=subscriptions,
            user=user,
            error=error,
        )
    return Rendered(content=FormResult(redirect=flair_url(community)))


@post('/in/{name:str}/api/flairtemplate')
async def flair_template(
    name: FromPath[str],
    data: URLEncodedBody[TemplateForm],
    flair: FlairDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    user: UserDep,
) -> Rendered:
    """Add a template, or change one by ``flair_template_id``."""
    community, standing = await visible(communities, user, name)
    try:
        saved = await service.save_template(
            flair,
            moderation,
            user.account,
            standing,
            community,
            kind=data.flair_type,
            template=data.flair_template_id,
            text=data.text,
            css_class=data.css_class,
            text_editable=truthy(data.text_editable),
        )
    except InaError as error:
        return await mod_change(
            community,
            standing,
            flair=flair,
            communities=communities,
            subscriptions=subscriptions,
            user=user,
            error=error,
        )
    result = FormResult(redirect=flair_url(community), data={'flair_template_id': str(saved.id)})
    return Rendered(content=result)


@post('/in/{name:str}/api/deleteflairtemplate')
async def delete_flair_template(
    name: FromPath[str],
    data: URLEncodedBody[TemplateForm],
    flair: FlairDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    user: UserDep,
) -> Rendered:
    community, standing = await visible(communities, user, name)
    await service.delete_template(
        flair, moderation, user.account, standing, community, data.flair_template_id
    )
    return Rendered(content=FormResult(redirect=flair_url(community)))


@post('/in/{name:str}/api/clearflairtemplates')
async def clear_flair_templates(
    name: FromPath[str],
    data: URLEncodedBody[TemplateForm],
    flair: FlairDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    user: UserDep,
) -> Rendered:
    community, standing = await visible(communities, user, name)
    await service.clear_templates(
        flair, moderation, user.account, standing, community, data.flair_type
    )
    return Rendered(content=FormResult(redirect=flair_url(community)))


@post('/in/{name:str}/api/flairselector', opt={CSRF_EXEMPT: True})
async def flair_selector(
    name: FromPath[str],
    data: URLEncodedBody[SelectForm],
    flair: FlairDep,
    accounts: AccountsDep,
    links: LinksDep,
    communities: CommunitiesDep,
    user: UserDep,
) -> FlairSelector:
    """The viewer's flair choices here, or a link's with ``link``, as r2's flairselector."""
    community, standing = await visible(communities, user, name)
    if data.link:
        link = await target_link(links, community, data.link)
        if not service.can_flair_link(user.account, link, community, standing):
            raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=HTTP_403_FORBIDDEN)
        current = service.Shown(
            link.flair_text or '', link.flair_css or '', community.settings.link_flair_position
        )
        choices = await flair.templates(community.id, FlairKind.LINK)
        return FlairSelector(
            current=current if link.flair_text or link.flair_css else None,
            position=community.settings.link_flair_position,
            choices=tuple(choices),
        )
    target = await target_account(accounts, data.name) if data.name else user.account
    if target.id != user.account.id and not service.may_moderate(standing):
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=HTTP_403_FORBIDDEN)
    found = await flair.user_flair(community.id, target.id)
    position = community.settings.flair_position
    mine: service.Shown | None = None
    if found is not None and (found.text or found.css_class):
        mine = service.Shown(found.text or '', found.css_class or '', position)
    choices = await flair.templates(community.id, FlairKind.USER)
    return FlairSelector(current=mine, position=position, choices=tuple(choices))


@post('/in/{name:str}/api/selectflair')
async def select_flair(
    name: FromPath[str],
    data: URLEncodedBody[SelectForm],
    flair: FlairDep,
    accounts: AccountsDep,
    links: LinksDep,
    moderation: ModerationDep,
    communities: CommunitiesDep,
    cache: CacheDep,
    user: UserDep,
) -> Rendered:
    """Choose a template for oneself (``name``) or one's link (``link``); none removes."""
    community, standing = await visible(communities, user, name)
    link = await target_link(links, community, data.link) if data.link else None
    target = None
    if link is None:
        target = await target_account(accounts, data.name) if data.name else user.account
    try:
        await service.select(
            flair,
            moderation,
            user.account,
            standing,
            community,
            link=link,
            target=target,
            template=data.flair_template_id,
            text=data.text,
        )
    except InaError as error:
        if error.status_code in {HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND}:
            raise
        return Rendered(content=FormResult(errors=error.errors, redirect=community.path))
    result = FormResult(redirect=local_path(data.dest, community.path))
    # A link's flair is in its cached copy and its search document.
    return Rendered(content=result, background=refreshed(cache, link) if link else None)


@post('/in/{name:str}/api/setflairenabled')
async def set_flair_enabled(
    name: FromPath[str],
    data: URLEncodedBody[EnabledForm],
    flair: FlairDep,
    communities: CommunitiesDep,
    user: UserDep,
) -> Rendered:
    """Whether one's own flair shows in this community."""
    community, _ = await visible(communities, user, name)
    await flair.set_flair_enabled(community.id, user.account.id, enabled=truthy(data.flair_enabled))
    return Rendered(content=FormResult(redirect=local_path(data.dest, community.path)))


async def flair_box(
    flair: FlairRepo, community: Community, standing: Standing, viewer: Authenticated | None
) -> FlairBox | None:
    """r2's sidebar box: whether one's flair shows here, how it looks, and what to pick from."""
    if viewer is None or not community.settings.flair_enabled:
        return None
    account = viewer.account
    found = await flair.user_flair(community.id, account.id)
    current = None
    if found is not None and (found.text or found.css_class):
        position = community.settings.flair_position
        current = service.Shown(found.text or '', found.css_class or '', position)
    choices: tuple[FlairTemplate, ...] = ()
    editable = False
    if service.can_flair_user(account, account.id, community, standing):
        choices = tuple(await flair.templates(community.id, FlairKind.USER))
        editable = service.may_moderate(standing) or any(t.text_editable for t in choices)
    return FlairBox(
        name=account.name,
        enabled=found.enabled if found else True,
        current=current,
        choices=choices,
        editable=editable,
    )
