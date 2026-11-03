"""The wiki, at r2's paths under ``/in/{name}/``: ``wiki/`` to read, ``api/wiki/`` to change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from litestar import Request, Response, get, post
from litestar.exceptions import MethodNotAllowedException, NotFoundException
from litestar.params import FromPath, FromQuery, URLEncodedBody
from litestar.response import Redirect
from litestar.status_codes import HTTP_403_FORBIDDEN

from app.controllers.auth import local_path, to_viewer
from app.controllers.communities import sidebar, visible
from app.controllers.deps import (
    AccountsDep,
    CommunitiesDep,
    ModerationDep,
    SettingsDep,
    StylesheetsDep,
    SubscriptionsDep,
    UserDep,
    ViewerDep,
    WikiDep,
)
from app.controllers.moderation import truthy
from app.controllers.render import Rendered
from app.errors import ErrorCode, InaError
from app.repos.base import (
    Community,
    CommunityRepo,
    ModerationRepo,
    StylesheetRepo,
    SubscriptionRepo,
    WikiRepo,
)
from app.services import wiki as service
from app.services.sessions import Authenticated
from app.services.stylesheets import Stylesheets
from app.views.communities import Sidebar
from app.views.forms import FormResult
from app.views.wiki import (
    REVISION_PREFIX,
    WikiEditPage,
    WikiPagesPage,
    WikiPageView,
    WikiRevisionsPage,
    WikiSettingsPage,
)


@dataclass
class EditForm:
    page: str = ''
    content: str = ''
    reason: str = ''
    previous: str = ''


@dataclass
class RevisionForm:
    page: str = ''
    revision: str = ''


@dataclass
class EditorForm:
    page: str = ''
    username: str = ''


@dataclass
class SettingsForm:
    permlevel: str = ''
    listed: str = ''


@dataclass(frozen=True, slots=True)
class Wiki:
    """A request's community, the viewer's place in its wiki, and what every wiki page shows."""

    community: Community
    who: service.WikiAccess
    side: Sidebar
    viewer: Authenticated | None

    def base(self) -> dict[str, Any]:
        return {
            'viewer': to_viewer(self.viewer),
            'community': self.community,
            'sidebar': self.side,
            'is_mod': self.who.mod,
        }


async def open_wiki(
    name: str,
    *,
    communities: CommunityRepo,
    subscriptions: SubscriptionRepo,
    moderation: ModerationRepo,
    viewer: Authenticated | None,
) -> Wiki:
    community, standing = await visible(communities, viewer, name)
    who = await service.access(moderation, viewer.account if viewer else None, standing, community)
    service.require_enabled(community, who)
    side = await sidebar(communities, subscriptions, viewer, community, standing)
    return Wiki(community, who, side, viewer)


def page_url(community: Community, page: str) -> str:
    return f'{community.path}wiki/{quote(page)}'


async def view_page(
    wiki: WikiRepo, ctx: Wiki, raw: str, v: str, v2: str
) -> WikiPageView | Response[Any]:
    name = service.normalize(raw)
    if raw and name != raw:
        # r2 sent a page asked for by another spelling to its normalized name.
        return Redirect(page_url(ctx.community, name))
    found = await service.viewable(wiki, ctx.community, ctx.who, name)
    may_revise = await service.revisable(wiki, ctx.community, ctx.who, found, name)
    if found is None:
        return WikiPageView(**ctx.base(), page=name, may_revise=may_revise, missing=True)
    content, revised_by, revised_at = found.content_md, found.revised_by, found.revised_at
    alert = service.DESCRIPTIONS.get(name) if ctx.who.mod else None
    diff = None
    if v:
        old = await service.revision_of(wiki, found, v)
        if old.hidden and not ctx.who.mod:
            raise InaError.of(ErrorCode.INVALID_REVISION, 'v', status_code=HTTP_403_FORBIDDEN)
        content, revised_by, revised_at = old.content_md, old.author_name, old.created
        alert = f'this page as it was {service.since(old.created)} ago'
        if v2:
            new = await service.revision_of(wiki, found, v2)
            first, second = service.since(old.created), service.since(new.created)
            alert = f'the changes between {first} ago and {second} ago'
            diff = service.diff(old.content_md, new.content_md, f'{first} ago', f'{second} ago')
            content, revised_by, revised_at = new.content_md, new.author_name, new.created
    return WikiPageView(
        **ctx.base(),
        page=name,
        may_revise=may_revise,
        alert=alert,
        content_md=content,
        content_html=service.render(name, content),
        code=name in service.CODE,
        revision_id=str(found.revision_id) if found.revision_id else None,
        revised_by=revised_by,
        revised_at=revised_at,
        diff=diff,
    )


async def edit_page(wiki: WikiRepo, ctx: Wiki, raw: str) -> WikiEditPage:
    name = service.normalize(raw)
    found = await service.viewable(wiki, ctx.community, ctx.who, name)
    if not await service.revisable(wiki, ctx.community, ctx.who, found, name):
        raise InaError.of(ErrorCode.MAY_NOT_REVISE, 'page', status_code=HTTP_403_FORBIDDEN)
    return WikiEditPage(
        **ctx.base(),
        page=name,
        may_revise=True,
        content=found.content_md if found else '',
        previous=str(found.revision_id) if found and found.revision_id else '',
    )


async def revisions_page(
    wiki: WikiRepo, ctx: Wiki, raw: str | None, after: str
) -> WikiRevisionsPage:
    page_id, name, may_revise = None, None, False
    if raw is not None:
        name = service.normalize(raw)
        found = await service.viewable(wiki, ctx.community, ctx.who, name)
        if found is None or not found.id:
            raise NotFoundException
        page_id = found.id
        may_revise = await service.revisable(wiki, ctx.community, ctx.who, found, name)
    before = service.revision_id(after.removeprefix(REVISION_PREFIX), 'after') if after else None
    revisions = await wiki.revisions(
        ctx.community.id,
        page_id=page_id,
        limit=service.PAGE_SIZE + 1,
        before=before,
        include_hidden=ctx.who.mod,
    )
    if raw is None and not ctx.who.mod:
        # The wiki's recent changes leave out pages the viewer may not see.
        pages = {page.name: page for page in await wiki.pages(ctx.community.id)}
        revisions = [
            r
            for r in revisions
            if service.may_view(ctx.who, r.page_name, pages[r.page_name].permlevel)
        ]
    shown = revisions[: service.PAGE_SIZE]
    more = len(revisions) > service.PAGE_SIZE
    return WikiRevisionsPage(
        **ctx.base(),
        page=name,
        may_revise=may_revise,
        revisions=tuple(shown),
        after=f'{REVISION_PREFIX}{shown[-1].id}' if more and shown else None,
    )


async def pages_page(wiki: WikiRepo, ctx: Wiki) -> WikiPagesPage:
    names = [
        page.name
        for page in await wiki.pages(ctx.community.id)
        if ctx.who.mod or (page.listed and service.may_view(ctx.who, page.name, page.permlevel))
    ]
    return WikiPagesPage(**ctx.base(), names=tuple(names))


async def settings_page(
    wiki: WikiRepo, ctx: Wiki, raw: str, errors: tuple[Any, ...] = ()
) -> WikiSettingsPage:
    name = service.normalize(raw)
    service.require_moderator(ctx.who, name)
    found = await service.existing(wiki, ctx.community, name)
    return WikiSettingsPage(
        **ctx.base(),
        page=name,
        may_revise=True,
        permlevel=found.permlevel,
        listed=found.listed,
        editors=tuple(await wiki.editors(found.id)),
        show_settings=not service.is_special(name),
        show_editors=not (service.is_restricted(name) and not service.is_special(name)),
        errors=errors,
    )


@get(['/in/{name:str}/wiki', '/in/{name:str}/wiki/{rest:path}'])
async def show_wiki(
    name: FromPath[str],
    wiki: WikiDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    viewer: ViewerDep,
    rest: FromPath[str] = '',
    v: FromQuery[str] = '',
    v2: FromQuery[str] = '',
    after: FromQuery[str] = '',
) -> Any:  # noqa: ANN401 - a page, or a redirect to the page's normalized name
    ctx = await open_wiki(
        name,
        communities=communities,
        subscriptions=subscriptions,
        moderation=moderation,
        viewer=viewer,
    )
    path = rest.strip('/')
    action, _, page = path.partition('/')
    if path == 'pages':
        return await pages_page(wiki, ctx)
    if path == 'revisions':
        return await revisions_page(wiki, ctx, None, after)
    if action == 'revisions' and page:
        return await revisions_page(wiki, ctx, page, after)
    if action in {'edit', 'create'} and page:
        return await edit_page(wiki, ctx, page)
    if action == 'settings' and page:
        return await settings_page(wiki, ctx, page)
    if action == 'discussions':
        raise NotFoundException
    return await view_page(wiki, ctx, path, v, v2)


def repos(
    wiki: WikiRepo,
    communities: CommunityRepo,
    moderation: ModerationRepo,
    stylesheets: StylesheetRepo | None = None,
    media: str = '',
) -> service.Repos:
    compiler = Stylesheets(stylesheets, media) if stylesheets is not None else None
    return service.Repos(
        wiki=wiki, communities=communities, moderation=moderation, stylesheets=compiler
    )


@post('/in/{name:str}/api/wiki/edit')
async def edit(
    name: FromPath[str],
    data: URLEncodedBody[EditForm],
    wiki: WikiDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    stylesheets: StylesheetsDep,
    settings: SettingsDep,
    user: UserDep,
) -> Rendered:
    """Save a revision of ``page``; ``previous`` notices a newer edit, as r2's did."""
    ctx = await open_wiki(
        name,
        communities=communities,
        subscriptions=subscriptions,
        moderation=moderation,
        viewer=user,
    )
    try:
        page = service.normalize(data.page)
        await service.edit(
            repos(wiki, communities, moderation, stylesheets, settings.media_url),
            user.account,
            ctx.who,
            ctx.community,
            page,
            data.content,
            reason=data.reason,
            previous=data.previous,
        )
    except InaError as error:
        # A page too long comes back to the form; not being allowed to edit is an error page.
        if error.status_code == HTTP_403_FORBIDDEN and error.errors[0].field != 'content':
            raise
        current = None
        if error.errors[0].code is ErrorCode.EDIT_CONFLICT:
            found = await wiki.page(ctx.community.id, service.normalize(data.page))
            current = found.content_md if found else ''
            previous = str(found.revision_id) if found and found.revision_id else ''
        else:
            previous = data.previous
        form = WikiEditPage(
            **ctx.base(),
            page=data.page,
            may_revise=True,
            content=data.content,
            reason=data.reason,
            previous=previous,
            current=current,
            errors=error.errors,
        )
        return Rendered(content=FormResult(errors=error.errors, page=form))
    return Rendered(content=FormResult(redirect=page_url(ctx.community, page)))


@post('/in/{name:str}/api/wiki/revert')
async def revert(
    name: FromPath[str],
    data: URLEncodedBody[RevisionForm],
    wiki: WikiDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    stylesheets: StylesheetsDep,
    settings: SettingsDep,
    user: UserDep,
) -> Rendered:
    ctx = await open_wiki(
        name,
        communities=communities,
        subscriptions=subscriptions,
        moderation=moderation,
        viewer=user,
    )
    page = service.normalize(data.page)
    await service.revert(
        repos(wiki, communities, moderation, stylesheets, settings.media_url),
        user.account,
        ctx.who,
        ctx.community,
        page,
        data.revision,
    )
    return Rendered(content=FormResult(redirect=page_url(ctx.community, page)))


@post('/in/{name:str}/api/wiki/hide')
async def hide(
    name: FromPath[str],
    data: URLEncodedBody[RevisionForm],
    wiki: WikiDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    user: UserDep,
) -> Rendered:
    """Hide a revision, or show it again; ``status`` is whether it is now hidden, as on r2."""
    ctx = await open_wiki(
        name,
        communities=communities,
        subscriptions=subscriptions,
        moderation=moderation,
        viewer=user,
    )
    page = service.normalize(data.page)
    hidden = await service.toggle_hidden(wiki, ctx.who, ctx.community, page, data.revision)
    dest = f'{ctx.community.path}wiki/revisions/{quote(page)}'
    return Rendered(content=FormResult(redirect=dest, data={'status': hidden}))


@post('/in/{name:str}/api/wiki/alloweditor/{act:str}')
async def allow_editor(
    name: FromPath[str],
    act: FromPath[str],
    data: URLEncodedBody[EditorForm],
    wiki: WikiDep,
    accounts: AccountsDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    user: UserDep,
) -> Rendered:
    if act not in {'add', 'del'}:
        raise NotFoundException
    ctx = await open_wiki(
        name,
        communities=communities,
        subscriptions=subscriptions,
        moderation=moderation,
        viewer=user,
    )
    page = service.normalize(data.page)
    dest = f'{ctx.community.path}wiki/settings/{quote(page)}'
    try:
        await service.change_editor(
            wiki, accounts, ctx.who, ctx.community, page, username=data.username, add=act == 'add'
        )
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN:
            raise
        form = await settings_page(wiki, ctx, page, error.errors)
        return Rendered(content=FormResult(errors=error.errors, page=form))
    return Rendered(content=FormResult(redirect=dest))


# The settings form posts to the settings page itself, as on r2. Its route is the wiki's catch-all,
# so the path is taken apart here as show_wiki does.
@post('/in/{name:str}/wiki/{rest:path}')
async def save_settings(
    request: Request[Any, Any, Any],
    name: FromPath[str],
    rest: FromPath[str],
    data: URLEncodedBody[SettingsForm],
    wiki: WikiDep,
    communities: CommunitiesDep,
    subscriptions: SubscriptionsDep,
    moderation: ModerationDep,
    user: UserDep,
) -> Rendered:
    """A page's permission level (``permlevel``) and whether it is ``listed``."""
    ctx = await open_wiki(
        name,
        communities=communities,
        subscriptions=subscriptions,
        moderation=moderation,
        viewer=user,
    )
    action, _, raw = rest.strip('/').partition('/')
    if action != 'settings' or not raw:
        raise MethodNotAllowedException
    page = service.normalize(raw)
    await service.change_settings(
        repos(wiki, communities, moderation),
        user.account,
        ctx.who,
        ctx.community,
        page,
        permlevel=data.permlevel,
        listed=truthy(data.listed),
    )
    dest = local_path(
        request.query_params.get('dest', ''), f'{ctx.community.path}wiki/settings/{page}'
    )
    return Rendered(content=FormResult(redirect=dest))
