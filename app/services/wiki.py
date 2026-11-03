"""A community's wiki, as r2 ran it: pages, their revisions, and who may read and edit them.

Who may do what follows r2's ``may_view`` and ``may_revise``. Moderators with the 'wiki'
permission may edit any ordinary page; ``config/`` pages need 'config' as well. Others edit
according to the community's wiki mode (disabled, modonly, anyone), its karma and account-age
minimums, and each page's permission level (0: as the community says; 1: only the page's
editors; 2: only moderators, who alone may also see it).

Four ``config/`` pages hold what the community settings page edits, and their content is kept on
the community too, as r2 kept it, so the pages that show it need no wiki lookup.
"""

from __future__ import annotations

import difflib
import posixpath
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import msgspec

from app.errors import ErrorCode, InaError
from app.repos.base import CommunityText, ModPermission, WikiConflictError, WikiPage
from app.services import bouncer
from app.services.perms import Action, Standing, can
from app.text import markdown
from app.views.format import age

if TYPE_CHECKING:
    from app.repos.base import (
        Account,
        AccountRepo,
        Community,
        CommunityRepo,
        Member,
        ModerationRepo,
        WikiRepo,
        WikiRevision,
    )
    from app.services.stylesheets import Stylesheets

FORBIDDEN = 403
NOT_FOUND = 404
CONFLICT = 409
INDEX = 'index'
STYLESHEET = 'config/stylesheet'
MAX_PAGE_BYTES = 256 * 1024
MAX_REASON = 256
PAGE_SIZE = 25
# r2 matched page names on bytes, so \w is ASCII here too.
PAGE_NAME = re.compile(r'^[\w\-/]+\Z', re.ASCII)
# Path segments that are the wiki's own pages, so no page may be named after them.
IMPOSSIBLE = ('edit/', 'revisions/', 'settings/', 'discussions/', 'pages/', 'create/')
RESTRICTED = ('ina/', 'config/', 'special/')
SPECIAL = frozenset(
    {
        'config/bouncer',
        'config/description',
        'config/sidebar',
        'config/stylesheet',
        'config/submit_text',
    }
)
SPECIAL_VIEW_LEVEL = {'config/bouncer': 2}
LENGTH_LIMITS = {
    'config/stylesheet': 128 * 1024,
    'config/submit_text': 1024,
    'config/sidebar': 5120,
    'config/description': 500,
}
# What the modlog says about an edit to these pages; others are "edited <name>".
MODACTIONS = {
    'config/bouncer': "changed Bouncer's rules",
    'config/description': 'changed the description',
    'config/sidebar': 'changed the sidebar',
    'config/submit_text': 'changed the text on the submit page',
    'config/stylesheet': 'changed the stylesheet',
}
# Shown to moderators above these pages: what each one changes.
DESCRIPTIONS = {
    'config/bouncer': "Bouncer's rules for this community, in YAML",
    'config/stylesheet': "This community's stylesheet; saving it restyles every page here",
    'config/submit_text': 'Shown to people as they submit a post here',
    'config/sidebar': 'The sidebar beside every page of this community',
    'config/description': (
        'A short description, shown in search results and to people this community is closed to'
    ),
}
# Pages whose content the community row also keeps.
MIRRORED = frozenset(
    {'config/bouncer', 'config/description', 'config/sidebar', 'config/submit_text'}
)
# Rendered as other user text rather than as a wiki page; the rest of config/ as code.
USERTEXT = frozenset({'config/description', 'config/sidebar', 'config/submit_text'})
CODE = frozenset({'config/bouncer', 'config/stylesheet'})
PERMLEVELS = (0, 1, 2)


def normalize(name: str) -> str:
    """r2's page names: lower case, spaces as underscores, no leading slash; empty is the index."""
    page = name.replace(' ', '_').lower()
    if page:
        page = posixpath.normpath(page).lstrip('/')
    if page and not PAGE_NAME.match(page):
        raise InaError.of(ErrorCode.INVALID_PAGE_NAME, 'page')
    page = page or INDEX
    if f'{page}/' in IMPOSSIBLE or page.startswith(IMPOSSIBLE):
        raise InaError.of(ErrorCode.INVALID_PAGE_NAME, 'page')
    return page


def is_special(name: str) -> bool:
    return name in SPECIAL


def is_restricted(name: str) -> bool:
    return f'{name}/' in RESTRICTED or name.startswith(RESTRICTED)


@dataclass(frozen=True, slots=True)
class WikiAccess:
    """An account's place in a community's wiki."""

    account: Account | None
    standing: Standing
    contributor: bool = False
    banned: bool = False

    @property
    def mod(self) -> bool:
        return self.standing.has(ModPermission.WIKI)

    def moderates(self, name: str) -> bool:
        """r2's wiki moderator: 'wiki', and 'config' too for a config page."""
        return self.mod and (not is_special(name) or self.standing.has(ModPermission.CONFIG))


async def access(
    moderation: ModerationRepo, account: Account | None, standing: Standing, community: Community
) -> WikiAccess:
    if account is None:
        return WikiAccess(None, standing)
    contributor, banned = await moderation.wiki_standing(community.id, account.id)
    return WikiAccess(account, standing, contributor, banned)


def require_enabled(community: Community, who: WikiAccess) -> None:
    """r2 let only moderators into a disabled wiki."""
    if community.settings.wikimode == 'disabled' and not who.mod:
        raise InaError.of(ErrorCode.WIKI_DISABLED, status_code=FORBIDDEN)


def may_view(who: WikiAccess, name: str, permlevel: int) -> bool:
    if who.mod:
        return True
    level = SPECIAL_VIEW_LEVEL.get(name, 0) if is_special(name) else permlevel
    return level < 2  # noqa: PLR2004 - level 2 is moderators only


def may_revise(  # noqa: C901, PLR0911 - r2's checks, in r2's order
    who: WikiAccess,
    community: Community,
    name: str,
    permlevel: int,
    *,
    editor: bool,
    now: datetime | None = None,
) -> bool:
    """r2's ``may_revise``, for an existing page or one about to be created."""
    account = who.account
    if account is None:
        return False
    special = is_special(name)
    if who.mod and not special:
        return True
    if is_restricted(name) and not special:
        return False
    if who.banned or who.standing.banned or not may_view(who, name, permlevel):
        return False
    if editor:
        return True
    if special:
        return who.standing.has(ModPermission.CONFIG)
    if permlevel > 0:
        return False
    if who.contributor:
        return True
    settings = community.settings
    if settings.wikimode != 'anyone':
        return False
    if not can(account, Action.SUBMIT, community, standing=who.standing):
        return False
    karma = max(account.link_karma, account.comment_karma)
    age = ((now or datetime.now(UTC)) - account.created).days
    return karma >= settings.wiki_edit_karma and age >= settings.wiki_edit_age


def mirrored(community: Community, name: str) -> str:
    """What the community row holds for a mirrored page."""
    return {
        'config/bouncer': community.bouncer_rules,
        'config/description': community.description_md,
        'config/sidebar': community.sidebar_md,
        'config/submit_text': community.settings.submit_text,
    }[name]


def render(name: str, content: str) -> str:
    if name in CODE:
        return ''
    if name in USERTEXT:
        return markdown.render(content)
    return markdown.render_wiki(content)


async def page(wiki: WikiRepo, community: Community, name: str) -> WikiPage | None:
    """The page, or a mirrored page's content from the community before its first wiki edit."""
    found = await wiki.page(community.id, name)
    if found is not None or name not in MIRRORED:
        return found
    content = mirrored(community, name)
    return WikiPage(
        id=0,
        community_id=community.id,
        name=name,
        permlevel=0,
        listed=True,
        content_md=content,
        content_html=render(name, content),
        revision_id=None,
        revised_by=None,
        revised_at=None,
    )


async def viewable(
    wiki: WikiRepo, community: Community, who: WikiAccess, name: str
) -> WikiPage | None:
    """The page if it exists and the account may see it; MAY_NOT_VIEW if it may not."""
    found = await page(wiki, community, name)
    if found is not None and not may_view(who, name, found.permlevel):
        raise InaError.of(ErrorCode.MAY_NOT_VIEW, 'page', status_code=FORBIDDEN)
    return found


async def revisable(
    wiki: WikiRepo, community: Community, who: WikiAccess, found: WikiPage | None, name: str
) -> bool:
    if found is None:
        # r2: only a moderator with 'config' creates special pages; nobody restricted ones.
        if is_restricted(name) and not (is_special(name) and who.moderates(name)):
            return False
        return may_revise(who, community, name, 0, editor=False)
    editor = (
        who.account is not None and found.id != 0 and await wiki.is_editor(found.id, who.account.id)
    )
    return may_revise(who, community, name, found.permlevel, editor=editor)


def revision_id(value: str, field: str = 'previous') -> UUID | None:
    if not value.strip():
        return None
    try:
        return UUID(value.strip())
    except ValueError:
        raise InaError.of(ErrorCode.INVALID_REVISION, field) from None


def _check(name: str, content: str) -> None:
    limit = LENGTH_LIMITS.get(name, MAX_PAGE_BYTES)
    if len(content.encode()) > limit:
        raise InaError.of(
            ErrorCode.CONTENT_LENGTH_ERROR, 'content', status_code=FORBIDDEN, max_length=limit
        )
    if name == 'config/bouncer':
        try:
            bouncer.parse(content)
        except bouncer.RuleError as error:
            raise InaError.of(ErrorCode.SPECIAL_ERRORS, 'content', error=str(error)) from None


async def _mirror(
    communities: CommunityRepo, community: Community, name: str, content: str
) -> None:
    if name == 'config/bouncer':
        await communities.set_bouncer_rules(community.id, content)
    elif name == 'config/submit_text':
        settings = msgspec.structs.replace(
            community.settings, submit_text=content, submit_text_html=markdown.render(content)
        )
        await communities.set_settings(community.id, settings)
    elif name in {'config/sidebar', 'config/description'}:
        text = CommunityText(
            title=community.title,
            description_md=community.description_md,
            description_html=community.description_html,
            sidebar_md=community.sidebar_md,
            sidebar_html=community.sidebar_html,
            type=community.type,
            nsfw=community.nsfw,
            settings=community.settings,
        )
        html = markdown.render(content)
        if name == 'config/sidebar':
            text = replace(text, sidebar_md=content, sidebar_html=html)
        else:
            text = replace(text, description_md=content, description_html=html)
        await communities.update(community.id, text)


@dataclass(frozen=True, slots=True, kw_only=True)
class Repos:
    wiki: WikiRepo
    communities: CommunityRepo
    moderation: ModerationRepo
    # Compiles config/stylesheet; the page cannot be saved without it.
    stylesheets: Stylesheets | None = None


async def _compile(repos: Repos, community: Community, name: str, content: str) -> str | None:
    """The stylesheet to serve, for config/stylesheet; None for every other page."""
    if name != STYLESHEET:
        return None
    if repos.stylesheets is None:
        raise InaError.of(ErrorCode.RESTRICTED_PAGE, 'page', status_code=FORBIDDEN)
    return await repos.stylesheets.compile(community.id, content)


async def _write(
    repos: Repos,
    actor: Account,
    who: WikiAccess,
    community: Community,
    name: str,
    content: str,
    *,
    reason: str,
    previous: UUID | None,
    found: WikiPage | None,
    compiled: str | None = None,
) -> WikiRevision:
    target = found if found is not None and found.id else None
    target = target or await repos.wiki.create_page(community.id, name)
    try:
        revision = await repos.wiki.revise(
            target.id,
            content_md=content,
            content_html=render(name, content),
            author_id=actor.id,
            reason=reason,
            previous=previous,
        )
    except WikiConflictError:
        raise InaError.of(ErrorCode.EDIT_CONFLICT, 'content', status_code=CONFLICT) from None
    if name in MIRRORED:
        await _mirror(repos.communities, community, name, content)
    if compiled is not None and repos.stylesheets is not None:
        await repos.stylesheets.store(community.id, compiled)
    if is_special(name) or who.mod:
        details = MODACTIONS.get(name, f'edited {name}')
        await repos.moderation.log(community.id, actor.id, 'wikirevise', details=details)
    return revision


async def edit(
    repos: Repos,
    actor: Account,
    who: WikiAccess,
    community: Community,
    name: str,
    content: str,
    *,
    reason: str = '',
    previous: str = '',
    check_enabled: bool = True,
) -> WikiRevision:
    """Save a new revision. ``previous`` is the revision the edit began from, if any.

    The stylesheet page saves config/stylesheet with ``check_enabled`` off: r2 let moderators
    with 'config' edit it whatever the wiki's mode.
    """
    if check_enabled:
        require_enabled(community, who)
    if len(reason) > MAX_REASON:
        raise InaError.of(ErrorCode.TOO_LONG, 'reason', max_length=MAX_REASON)
    found = await viewable(repos.wiki, community, who, name)
    if not await revisable(repos.wiki, community, who, found, name):
        # r2: RESTRICTED_PAGE for creating one, MAY_NOT_REVISE for editing a page that exists.
        restricted = found is None and is_restricted(name)
        code = ErrorCode.RESTRICTED_PAGE if restricted else ErrorCode.MAY_NOT_REVISE
        raise InaError.of(code, 'page', status_code=FORBIDDEN)
    _check(name, content)
    compiled = await _compile(repos, community, name, content)
    return await _write(
        repos,
        actor,
        who,
        community,
        name,
        content,
        reason=reason.strip(),
        previous=revision_id(previous),
        found=found,
        compiled=compiled,
    )


def require_moderator(who: WikiAccess, name: str) -> None:
    if not who.moderates(name):
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)


async def existing(wiki: WikiRepo, community: Community, name: str) -> WikiPage:
    found = await wiki.page(community.id, name)
    if found is None:
        raise InaError.of(ErrorCode.PAGE_NOT_FOUND, 'page', status_code=NOT_FOUND)
    return found


async def revision_of(wiki: WikiRepo, found: WikiPage, value: str) -> WikiRevision:
    wanted = revision_id(value, 'revision')
    revision = await wiki.revision(found.id, wanted) if wanted else None
    if revision is None:
        raise InaError.of(ErrorCode.INVALID_REVISION, 'revision', status_code=NOT_FOUND)
    return revision


def since(then: datetime, now: datetime | None = None) -> str:
    """r2's timesince, for revert reasons."""
    return age(then, now or datetime.now(UTC)).removesuffix(' ago')


async def revert(
    repos: Repos, actor: Account, who: WikiAccess, community: Community, name: str, value: str
) -> WikiRevision:
    """Make an old revision's content current again, as a new revision."""
    require_moderator(who, name)
    found = await existing(repos.wiki, community, name)
    old = await revision_of(repos.wiki, found, value)
    _check(name, old.content_md)
    compiled = await _compile(repos, community, name, old.content_md)
    return await _write(
        repos,
        actor,
        who,
        community,
        name,
        old.content_md,
        reason=f'reverted back {since(old.created)}',
        previous=None,
        found=found,
        compiled=compiled,
    )


async def toggle_hidden(
    wiki: WikiRepo, who: WikiAccess, community: Community, name: str, value: str
) -> bool:
    """Hide a revision from everyone but moderators, or show it again; returns the new state."""
    require_moderator(who, name)
    found = await existing(wiki, community, name)
    revision = await revision_of(wiki, found, value)
    await wiki.set_hidden(revision.id, hidden=not revision.hidden)
    return not revision.hidden


async def change_settings(
    repos: Repos,
    actor: Account,
    who: WikiAccess,
    community: Community,
    name: str,
    *,
    permlevel: str,
    listed: bool,
) -> WikiPage:
    require_moderator(who, name)
    found = await existing(repos.wiki, community, name)
    try:
        level = int(permlevel)
    except ValueError:
        level = -1
    if level not in PERMLEVELS or is_special(name):
        raise InaError.of(ErrorCode.INVALID_PERMLEVEL, 'permlevel', status_code=FORBIDDEN)
    await repos.wiki.set_page_settings(found.id, permlevel=level, listed=listed)
    if found.listed != listed:
        verb = 'Relisted' if listed else 'Delisted'
        await repos.moderation.log(
            community.id, actor.id, 'wikipagelisted', details=f'{verb} page {name}'
        )
    if found.permlevel != level:
        details = f'Page: {name}, Changed from {found.permlevel} to {level}'
        await repos.moderation.log(community.id, actor.id, 'wikipermlevel', details=details)
    return replace(found, permlevel=level, listed=listed)


async def change_editor(
    wiki: WikiRepo,
    accounts: AccountRepo,
    who: WikiAccess,
    community: Community,
    name: str,
    *,
    username: str,
    add: bool,
) -> list[Member]:
    """Let one account edit a page whatever its permission level, or stop letting it."""
    require_moderator(who, name)
    found = await existing(wiki, community, name)
    target = await accounts.by_name(username.strip()) if username.strip() else None
    if target is None or target.deleted:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, 'username', status_code=NOT_FOUND)
    if add:
        await wiki.add_editor(found.id, target.id)
    else:
        await wiki.remove_editor(found.id, target.id)
    return await wiki.editors(found.id)


async def record_settings(
    wiki: WikiRepo, actor: Account, before: Community, after: Community
) -> None:
    """Keep the config pages' history when the community settings page changes their content."""
    for name in sorted(MIRRORED - {'config/bouncer'}):
        content = mirrored(after, name)
        if content == mirrored(before, name):
            continue
        target = await wiki.create_page(after.id, name)
        await wiki.revise(
            target.id,
            content_md=content,
            content_html=render(name, content),
            author_id=actor.id,
            reason='Updated from the community settings',
            previous=None,
        )


def diff(old: str, new: str, old_label: str, new_label: str) -> str:
    """r2's comparison table: difflib's, which escapes the text it shows."""
    table = difflib.HtmlDiff(wrapcolumn=60)
    return table.make_table(
        old.splitlines(), new.splitlines(), fromdesc=old_label, todesc=new_label, context=True
    )
