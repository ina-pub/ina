"""Saving and hiding: r2's /api/save, /api/unsave, /api/hide and /api/unhide."""

from __future__ import annotations

from dataclasses import dataclass

from litestar import post
from litestar.params import URLEncodedBody

from app.controllers.auth import WHILE_SUSPENDED, local_path
from app.controllers.communities import visible
from app.controllers.deps import CommentsDep, CommunitiesDep, LinksDep, MarksDep, UserDep
from app.controllers.moderation import thing_and_link
from app.controllers.render import Rendered
from app.errors import ErrorCode, InaError
from app.ids import Kind
from app.repos.base import CommentRepo, CommunityRepo, LinkRepo, Mark, MarkRepo
from app.services.sessions import Authenticated
from app.views.forms import FormResult


@dataclass
class MarkForm:
    # One fullname, or several separated by commas, as r2's hide took them.
    id: str = ''
    dest: str = ''


async def marking(
    mark: Mark,
    *,
    add: bool,
    data: MarkForm,
    links: LinkRepo,
    comments: CommentRepo,
    communities: CommunityRepo,
    marks: MarkRepo,
    user: Authenticated,
) -> Rendered:
    for name in filter(None, (part.strip() for part in data.id.split(','))):
        thing, link = await thing_and_link(links, comments, name)
        if mark is Mark.HIDDEN and thing.fullname.kind is not Kind.LINK:
            raise InaError.of(ErrorCode.NO_THING_ID, 'id')
        if add:
            await visible(communities, user, link.community_name)
            await marks.add(user.account.id, mark, thing.fullname)
        else:
            await marks.remove(user.account.id, mark, thing.fullname)
    return Rendered(content=FormResult(redirect=local_path(data.dest, '/')))


@post('/api/save', opt={WHILE_SUSPENDED: True})
async def save(
    data: URLEncodedBody[MarkForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    marks: MarksDep,
    user: UserDep,
) -> Rendered:
    return await marking(
        Mark.SAVED,
        add=True,
        data=data,
        links=links,
        comments=comments,
        communities=communities,
        marks=marks,
        user=user,
    )


@post('/api/unsave', opt={WHILE_SUSPENDED: True})
async def unsave(
    data: URLEncodedBody[MarkForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    marks: MarksDep,
    user: UserDep,
) -> Rendered:
    return await marking(
        Mark.SAVED,
        add=False,
        data=data,
        links=links,
        comments=comments,
        communities=communities,
        marks=marks,
        user=user,
    )


@post('/api/hide', opt={WHILE_SUSPENDED: True})
async def hide(
    data: URLEncodedBody[MarkForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    marks: MarksDep,
    user: UserDep,
) -> Rendered:
    return await marking(
        Mark.HIDDEN,
        add=True,
        data=data,
        links=links,
        comments=comments,
        communities=communities,
        marks=marks,
        user=user,
    )


@post('/api/unhide', opt={WHILE_SUSPENDED: True})
async def unhide(
    data: URLEncodedBody[MarkForm],
    links: LinksDep,
    comments: CommentsDep,
    communities: CommunitiesDep,
    marks: MarksDep,
    user: UserDep,
) -> Rendered:
    return await marking(
        Mark.HIDDEN,
        add=False,
        data=data,
        links=links,
        comments=comments,
        communities=communities,
        marks=marks,
        user=user,
    )
