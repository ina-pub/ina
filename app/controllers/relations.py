"""Friends and blocks between accounts.

r2's /api/friend and /api/unfriend without a community, /api/block, and the /prefs/friends and
/prefs/blocked lists.
"""

from __future__ import annotations

from dataclasses import dataclass

from litestar import get, post
from litestar.params import URLEncodedBody
from litestar.status_codes import HTTP_403_FORBIDDEN

from app.controllers.auth import WHILE_SUSPENDED, local_path, to_viewer
from app.controllers.deps import AccountsDep, CommentsDep, LinksDep, MessagesDep, UserDep
from app.controllers.render import Rendered
from app.errors import ErrorCode, FieldError, InaError
from app.ids import Fullname, Kind
from app.repos.base import AccountRepo
from app.services import relations
from app.services.moderation import RelationForm
from app.services.sessions import Authenticated
from app.views.forms import FormResult
from app.views.relations import RelationsPage

# Each relation's list in the preferences.
PAGES = {relations.FRIEND: '/prefs/friends', relations.ENEMY: '/prefs/blocked'}


async def relations_page(
    accounts: AccountRepo, user: Authenticated, kind: str, errors: tuple[FieldError, ...] = ()
) -> RelationsPage:
    members = await accounts.related(user.account.id, kind)
    return RelationsPage(viewer=to_viewer(user), kind=kind, members=tuple(members), errors=errors)


async def change_relation(
    data: RelationForm, dest: str, accounts: AccountRepo, user: Authenticated, *, add: bool
) -> Rendered:
    """Befriend, block, or undo either, for /api/friend and /api/unfriend."""
    try:
        if add:
            await relations.add(accounts, user.account, data.name, data.type, note=data.note)
        else:
            await relations.remove(accounts, user.account, data.name, data.type)
    except InaError as error:
        if error.status_code == HTTP_403_FORBIDDEN or data.type not in PAGES:
            raise
        page = await relations_page(accounts, user, data.type, error.errors)
        return Rendered(content=FormResult(errors=error.errors, page=page))
    return Rendered(content=FormResult(redirect=local_path(dest, PAGES[data.type])))


@get('/prefs/friends')
async def show_friends(accounts: AccountsDep, user: UserDep) -> RelationsPage:
    return await relations_page(accounts, user, relations.FRIEND)


@get('/prefs/blocked')
async def show_blocked(accounts: AccountsDep, user: UserDep) -> RelationsPage:
    return await relations_page(accounts, user, relations.ENEMY)


@dataclass
class BlockForm:
    # The fullname of a message, comment or link from the account to block.
    id: str = ''
    dest: str = ''


@post('/api/block', opt={WHILE_SUSPENDED: True})
async def block(
    data: URLEncodedBody[BlockForm],
    accounts: AccountsDep,
    links: LinksDep,
    comments: CommentsDep,
    messages: MessagesDep,
    user: UserDep,
) -> Rendered:
    """Block whoever sent a message or wrote a comment or link, as r2's block button did."""
    try:
        thing = Fullname.parse(data.id)
    except ValueError:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id') from None
    author_id: int | None = None
    if thing.kind is Kind.MESSAGE and (message := await messages.by_id(thing.id)):
        # Only something one received: a block is the recipient's.
        if user.account.id == message.to_id:
            author_id = message.from_id
    elif thing.kind is Kind.COMMENT and (comment := await comments.by_id(thing.id)):
        author_id = comment.author_id
    elif thing.kind is Kind.LINK and (link := await links.by_id(thing.id)):
        author_id = link.author_id
    author = await accounts.by_id(author_id) if author_id is not None else None
    if author is None:
        raise InaError.of(ErrorCode.NO_THING_ID, 'id')
    await relations.add(accounts, user.account, author.name, relations.ENEMY)
    return Rendered(content=FormResult(redirect=local_path(data.dest, '/message/inbox/')))
