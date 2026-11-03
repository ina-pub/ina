"""Moderating a community, as r2 did: its lists, actions on things, and the modlog.

Every change a moderator makes is logged with r2's action names. Who may do what follows r2:
bans and approved submitters need 'access'; acting on links and comments needs 'posts'; inviting
moderators and changing their permissions needs full permissions, and a moderator can only remove
or change those added after them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import msgspec

from app.errors import ErrorCode, InaError
from app.ids import Fullname, Kind
from app.repos.base import ModPermission, NewMessage
from app.services.perms import Action, Standing, can
from app.text import markdown

if TYPE_CHECKING:
    from app.repos.base import (
        Account,
        AccountRepo,
        AdminRepo,
        Comment,
        Community,
        CommunityRepo,
        Link,
        Message,
        MessageRepo,
        ModerationRepo,
    )

PERMISSION_NAMES = {
    'access': ModPermission.ACCESS,
    'config': ModPermission.CONFIG,
    'flair': ModPermission.FLAIR,
    'mail': ModPermission.MAIL,
    'posts': ModPermission.POSTS,
    'wiki': ModPermission.WIKI,
}
MAX_STICKIES = 2
MAX_BAN_DAYS = 999
MAX_NOTE = 300
MAX_REASON = 100
FORBIDDEN = 403
RELATIONS = (
    'banned',
    'muted',
    'contributor',
    'moderator_invite',
    'moderator',
    'wikibanned',
    'wikicontributor',
)
# How long a modmail mute lasts, as r2's did.
MUTE_DAYS = 3
# The relations kept for the wiki, which need the 'wiki' permission.
WIKI_RELATIONS = ('wikibanned', 'wikicontributor')


def parse_permissions(text: str) -> ModPermission:
    """r2's permission strings: '+all', or '-all,+posts,+mail'. Empty means everything."""
    granted = ModPermission(0)
    parts = [part.strip() for part in text.split(',') if part.strip()] or ['+all']
    for part in parts:
        sign, name = part[0], part[1:]
        if sign not in '+-' or (name != 'all' and name not in PERMISSION_NAMES):
            raise InaError.of(ErrorCode.INVALID_PERMISSIONS, 'permissions')
        flag = ModPermission.ALL if name == 'all' else PERMISSION_NAMES[name]
        granted = granted | flag if sign == '+' else granted & ~flag
    return granted


def permissions_text(permissions: ModPermission) -> list[str]:
    """As r2 listed them: ['all'], or the names held."""
    if permissions == ModPermission.ALL:
        return ['all']
    return [name for name, flag in PERMISSION_NAMES.items() if permissions & flag == flag]


def require(actor: Account, action: Action, community: Community, standing: Standing) -> None:
    if not can(actor, action, community, standing=standing):
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)


@dataclass
class RelationForm:
    """``/api/friend`` and ``/api/unfriend``, with r2's field names."""

    name: str = ''
    type: str = ''
    permissions: str = ''
    note: str = ''
    ban_reason: str = ''
    # Days; empty for a permanent ban.
    duration: str = ''
    ban_message: str = ''


@dataclass(frozen=True, slots=True)
class Notice:
    """A message to send on the moderators' behalf, after the change commits."""

    message: Message


async def _target(accounts: AccountRepo, name: str) -> Account:
    account = await accounts.by_name(name.strip()) if name.strip() else None
    if account is None or account.deleted:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, 'name')
    return account


async def _notify(
    messages: MessageRepo,
    actor: Account,
    community: Community,
    to: Account,
    subject: str,
    body: str,
) -> Notice:
    """A message from the community itself, as r2 sent ban and invite notices."""
    message = await messages.create(
        NewMessage(
            from_id=actor.id,
            to_id=to.id,
            community_id=community.id,
            parent=None,
            subject=subject,
            body_md=body,
            body_html=markdown.render(body),
            from_community=True,
            ip='',
            created=datetime.now(UTC),
        )
    )
    return Notice(message)


def _expires(duration: str) -> datetime | None:
    if not duration.strip():
        return None
    try:
        days = int(duration)
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_BAN_DAYS:
        raise InaError.of(ErrorCode.BAD_NUMBER, 'duration', range=f'1 - {MAX_BAN_DAYS}')
    return datetime.now(UTC) + timedelta(days=days)


async def add_relation(
    moderation: ModerationRepo,
    communities: CommunityRepo,
    accounts: AccountRepo,
    messages: MessageRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    form: RelationForm,
) -> Notice | None:
    """Ban, approve as a submitter, or invite to moderate; returns a notice to deliver."""
    if form.type not in {'banned', 'muted', 'contributor', 'moderator_invite', *WIKI_RELATIONS}:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'type')
    target = await _target(accounts, form.name)
    mod, _, banned = await communities.standing(community.id, target.id)
    if form.type == 'muted':
        require(actor, Action.MANAGE_MAIL, community, standing)
        if mod is not None:
            raise InaError.of(ErrorCode.CANT_RESTRICT_MODERATOR, 'name')
        return await _mute(moderation, messages, actor, community, target)
    if form.type in WIKI_RELATIONS:
        require(actor, Action.MANAGE_WIKI, community, standing)
        await _wiki_relation(moderation, actor, community, target, form, is_mod=mod is not None)
        return None
    if form.type == 'moderator_invite':
        require(actor, Action.MANAGE_MODERATORS, community, standing)
        if mod is not None:
            raise InaError.of(ErrorCode.ALREADY_MODERATOR, 'name')
        if banned:
            raise InaError.of(ErrorCode.BANNED_FROM_COMMUNITY, 'name')
        return await _invite(moderation, messages, actor, community, target, form)
    require(actor, Action.MANAGE_ACCESS, community, standing)
    return await _access_relation(
        moderation, messages, actor, community, target, form, is_mod=mod is not None, banned=banned
    )


async def _access_relation(
    moderation: ModerationRepo,
    messages: MessageRepo,
    actor: Account,
    community: Community,
    target: Account,
    form: RelationForm,
    *,
    is_mod: bool,
    banned: bool,
) -> Notice | None:
    """Approve as a submitter, or ban: r2's 'access' permission."""
    if form.type == 'contributor':
        if banned:
            raise InaError.of(ErrorCode.BANNED_FROM_COMMUNITY, 'name')
        if await moderation.add_contributor(community.id, target.id, by=actor.id):
            await moderation.log(community.id, actor.id, 'addcontributor', target.fullname)
        return None
    if is_mod:
        raise InaError.of(ErrorCode.CANT_RESTRICT_MODERATOR, 'name')
    return await _ban(moderation, messages, actor, community, target, form)


async def _wiki_relation(
    moderation: ModerationRepo,
    actor: Account,
    community: Community,
    target: Account,
    form: RelationForm,
    *,
    is_mod: bool,
) -> None:
    if form.type == 'wikicontributor':
        if await moderation.add_wiki_contributor(community.id, target.id, by=actor.id):
            await moderation.log(community.id, actor.id, 'wikicontributor', target.fullname)
        return
    if is_mod:
        raise InaError.of(ErrorCode.CANT_RESTRICT_MODERATOR, 'name')
    if len(form.note) > MAX_NOTE:
        raise InaError.of(ErrorCode.TOO_LONG, 'note', max_length=MAX_NOTE)
    expires = _expires(form.duration)
    await moderation.wiki_ban(
        community.id, target.id, note=form.note.strip(), expires=expires, by=actor.id
    )
    length = f'{form.duration.strip()} days' if expires else 'permanent'
    await moderation.log(community.id, actor.id, 'wikibanned', target.fullname, length)


async def _invite(
    moderation: ModerationRepo,
    messages: MessageRepo,
    actor: Account,
    community: Community,
    target: Account,
    form: RelationForm,
) -> Notice:
    permissions = parse_permissions(form.permissions)
    await moderation.invite(community.id, target.id, permissions=permissions, by=actor.id)
    await moderation.log(community.id, actor.id, 'invitemoderator', target.fullname)
    body = (
        f'The innkeepers of [in/{community.name}](/in/{community.name}/) would like you to join '
        f'them. You can say yes or no on [their list of innkeepers]'
        f'(/in/{community.name}/about/moderators).'
    )
    subject = f'an invitation to moderate in/{community.name}'
    return await _notify(messages, actor, community, target, subject, body)


async def _ban(
    moderation: ModerationRepo,
    messages: MessageRepo,
    actor: Account,
    community: Community,
    target: Account,
    form: RelationForm,
) -> Notice:
    for field, value, limit in (
        ('note', form.note, MAX_NOTE),
        ('ban_reason', form.ban_reason, MAX_REASON),
    ):
        if len(value) > limit:
            raise InaError.of(ErrorCode.TOO_LONG, field, max_length=limit)
    expires = _expires(form.duration)
    # r2 folded the reason into the note.
    note = ': '.join(part for part in (form.ban_reason.strip(), form.note.strip()) if part)
    await moderation.ban(
        community.id, target.id, note=note, message=form.ban_message, expires=expires, by=actor.id
    )
    length = f'{form.duration.strip()} days' if expires else 'permanent'
    await moderation.log(community.id, actor.id, 'banuser', target.fullname, length)
    body = (
        f'You can no longer post or comment in [in/{community.name}](/in/{community.name}/) '
        f'({length}).'
    )
    if form.ban_message.strip():
        body += f'\n\nThe innkeepers added:\n\n{form.ban_message}'
    subject = f'banned from in/{community.name}'
    return await _notify(messages, actor, community, target, subject, body)


async def _mute(
    moderation: ModerationRepo,
    messages: MessageRepo,
    actor: Account,
    community: Community,
    target: Account,
) -> Notice:
    expires = datetime.now(UTC) + timedelta(days=MUTE_DAYS)
    await moderation.mute(community.id, target.id, expires=expires, by=actor.id)
    await moderation.log(community.id, actor.id, 'muteuser', target.fullname)
    subject = f'muted from in/{community.name} modmail'
    body = (
        f'Messages from you to the innkeepers of [in/{community.name}](/in/{community.name}/) '
        f'will not go through for the next {MUTE_DAYS} days.'
    )
    return await _notify(messages, actor, community, target, subject, body)


async def _seniority(communities: CommunityRepo, community: Community) -> list[int]:
    return [moderator.account_id for moderator in await communities.moderators(community.id)]


async def can_demod(
    communities: CommunityRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    target: int,
) -> bool:
    """Anyone may step down; a full moderator may remove those added after them, as on r2."""
    if actor.id == target:
        return True
    order = await _seniority(communities, community)
    if not standing.full_moderator or actor.id not in order or target not in order:
        return False
    return order.index(actor.id) < order.index(target)


async def remove_relation(
    moderation: ModerationRepo,
    communities: CommunityRepo,
    accounts: AccountRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    form: RelationForm,
) -> None:
    if form.type not in RELATIONS:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'type')
    target = await _target(accounts, form.name)
    if form.type in {'moderator', 'moderator_invite'}:
        await _remove_moderator(moderation, communities, actor, standing, community, target, form)
        return
    if form.type in WIKI_RELATIONS:
        require(actor, Action.MANAGE_WIKI, community, standing)
        await _remove_wiki_relation(moderation, actor, community, target, form.type)
        return
    if form.type == 'muted':
        require(actor, Action.MANAGE_MAIL, community, standing)
        if await moderation.unmute(community.id, target.id):
            await moderation.log(community.id, actor.id, 'unmuteuser', target.fullname)
        return
    require(actor, Action.MANAGE_ACCESS, community, standing)
    if form.type == 'banned' and await moderation.unban(community.id, target.id):
        await moderation.log(community.id, actor.id, 'unbanuser', target.fullname)
    elif form.type == 'contributor' and await moderation.remove_contributor(
        community.id, target.id
    ):
        await moderation.log(community.id, actor.id, 'removecontributor', target.fullname)


async def _remove_moderator(
    moderation: ModerationRepo,
    communities: CommunityRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    target: Account,
    form: RelationForm,
) -> None:
    """Remove a moderator, or take back an invitation to moderate."""
    if form.type == 'moderator':
        if not await can_demod(communities, actor, standing, community, target.id):
            raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)
        if await moderation.remove_moderator(community.id, target.id):
            details = 'remove_self' if target.id == actor.id else ''
            await moderation.log(
                community.id, actor.id, 'removemoderator', target.fullname, details
            )
        return
    require(actor, Action.MANAGE_MODERATORS, community, standing)
    if await moderation.take_invite(community.id, target.id) is not None:
        await moderation.log(community.id, actor.id, 'uninvitemoderator', target.fullname)


async def _remove_wiki_relation(
    moderation: ModerationRepo, actor: Account, community: Community, target: Account, kind: str
) -> None:
    if kind == 'wikibanned' and await moderation.wiki_unban(community.id, target.id):
        await moderation.log(community.id, actor.id, 'wikiunbanned', target.fullname)
    elif kind == 'wikicontributor' and await moderation.remove_wiki_contributor(
        community.id, target.id
    ):
        await moderation.log(community.id, actor.id, 'removewikicontributor', target.fullname)


async def set_permissions(
    moderation: ModerationRepo,
    communities: CommunityRepo,
    accounts: AccountRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    form: RelationForm,
) -> None:
    """Change a moderator's or an invite's permissions."""
    require(actor, Action.MANAGE_MODERATORS, community, standing)
    target = await _target(accounts, form.name)
    permissions = parse_permissions(form.permissions)
    if form.type == 'moderator_invite':
        if await moderation.take_invite(community.id, target.id) is None:
            raise InaError.of(ErrorCode.NO_INVITE_FOUND, 'name')
        await moderation.invite(community.id, target.id, permissions=permissions, by=actor.id)
    elif form.type == 'moderator':
        if target.id == actor.id or not await can_demod(
            communities, actor, standing, community, target.id
        ):
            raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)
        await moderation.set_permissions(community.id, target.id, permissions)
    else:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'type')
    details = ','.join(f'+{name}' for name in permissions_text(permissions)) or '-all'
    await moderation.log(community.id, actor.id, 'setpermissions', target.fullname, details)


async def accept_invite(
    moderation: ModerationRepo, communities: CommunityRepo, account: Account, community: Community
) -> None:
    permissions = await moderation.take_invite(community.id, account.id)
    if permissions is None:
        raise InaError.of(ErrorCode.NO_INVITE_FOUND, 'community')
    await communities.add_moderator(
        community.id, account.id, permissions=permissions, added_by=None
    )
    await moderation.log(community.id, account.id, 'acceptmoderatorinvite', account.fullname)


def _keep_admin_removal(actor: Account, removed_by: str | None) -> None:
    """What the site's admins removed, only an admin may put back or remove again."""
    if removed_by == 'admin' and not actor.is_admin:
        raise InaError.of(ErrorCode.ADMIN_REMOVED, status_code=FORBIDDEN)


async def _log_admin(
    admin_log: AdminRepo | None,
    actor: Account,
    standing: Standing,
    action: str,
    thing: Fullname,
    details: str = '',
) -> None:
    """What an admin does in a community they don't keep goes in the admin log as well."""
    if admin_log is not None and standing.admin:
        await admin_log.log(actor.id, action, thing, details)


def _action(thing: Fullname, verb: str) -> str:
    return f'{verb}{"link" if thing.kind is Kind.LINK else "comment"}'


async def remove(
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    thing: Fullname,
    *,
    spam: bool,
    removed_by: str = 'mod',
    details: str = '',
    was_removed_by: str | None = None,
    admin_log: AdminRepo | None = None,
) -> None:
    """Remove a link or comment; an admin not among the innkeepers removes it as an admin."""
    require(actor, Action.MODERATE, community, standing)
    _keep_admin_removal(actor, was_removed_by)
    if standing.admin:
        removed_by = 'admin'
    await moderation.set_removed(thing, removed=True, spam=spam, removed_by=removed_by)
    action = _action(thing, 'spam' if spam else 'remove')
    await moderation.log(community.id, actor.id, action, thing, details)
    await _log_admin(admin_log, actor, standing, action, thing, details)


async def approve(
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    thing: Fullname,
    *,
    was_removed_by: str | None = None,
    admin_log: AdminRepo | None = None,
) -> None:
    require(actor, Action.MODERATE, community, standing)
    _keep_admin_removal(actor, was_removed_by)
    await moderation.set_approved(thing, by=actor.id, at=datetime.now(UTC))
    await moderation.log(community.id, actor.id, _action(thing, 'approve'), thing)
    await _log_admin(admin_log, actor, standing, _action(thing, 'approve'), thing)


async def ignore_reports(
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    thing: Fullname,
    *,
    ignore: bool,
) -> None:
    require(actor, Action.MODERATE, community, standing)
    await moderation.set_ignore_reports(thing, ignore=ignore)
    action = 'ignorereports' if ignore else 'unignorereports'
    await moderation.log(community.id, actor.id, action, thing)


# What /api/distinguish's how stores, as r2 had it; no clears it.
DISTINGUISHED = {'yes': 'moderator', 'admin': 'admin'}


async def distinguish(
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    thing: Link | Comment,
    how: str,
    *,
    sticky: bool = False,
    admin_log: AdminRepo | None = None,
) -> None:
    """Mark one's own post as said as an innkeeper, as an admin, or as neither.

    ``how`` is yes (an innkeeper), admin (in admin mode) or no. ``sticky`` pins a top-level
    comment.
    """
    allowed = {
        # An admin outside the community's innkeepers speaks as an admin, not as one of them.
        'yes': standing.mod is not None and not standing.admin,
        'admin': actor.is_admin,
        'no': standing.mod is not None,
    }
    if how not in allowed:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'how')
    if thing.author_id != actor.id or not allowed[how]:
        raise InaError.of(ErrorCode.MOD_REQUIRED, status_code=FORBIDDEN)
    await moderation.set_distinguished(thing.fullname, DISTINGUISHED.get(how))
    comment_on = getattr(thing, 'link_id', None)
    if comment_on is not None and getattr(thing, 'parent_id', None) is None:
        await moderation.set_sticky_comment(
            comment_on, thing.id if sticky and how != 'no' else None
        )
    await moderation.log(community.id, actor.id, 'distinguish', thing.fullname, how)
    if how == 'admin' or thing.distinguished == 'admin':
        await _log_admin(admin_log, actor, Standing(admin=True), 'distinguish', thing.fullname, how)


async def sticky(
    moderation: ModerationRepo,
    communities: CommunityRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    link: Link,
    *,
    state: bool,
    num: int | None = None,
) -> None:
    """Pin a link above the hot listing, at most two at once; a third takes slot ``num``."""
    require(actor, Action.MODERATE, community, standing)
    name = str(link.fullname)
    pinned = [f for f in community.settings.sticky_fullnames if f != name]
    if state:
        if len(pinned) >= MAX_STICKIES:
            slot = (num or MAX_STICKIES) - 1
            replaced = pinned.pop(min(max(slot, 0), len(pinned) - 1))
            await moderation.set_link_flags(Fullname.parse(replaced).id, stickied=False)
        position = (num - 1) if num else len(pinned)
        pinned.insert(min(max(position, 0), len(pinned)), name)
    settings = msgspec.structs.replace(community.settings, sticky_fullnames=tuple(pinned))
    await communities.set_settings(community.id, settings)
    await moderation.set_link_flags(link.id, stickied=state)
    await moderation.log(community.id, actor.id, 'sticky' if state else 'unsticky', link.fullname)


async def lock(
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    link: Link,
    *,
    locked: bool,
) -> None:
    require(actor, Action.MODERATE, community, standing)
    await moderation.set_link_flags(link.id, locked=locked)
    await moderation.log(community.id, actor.id, 'lock' if locked else 'unlock', link.fullname)


async def mark_nsfw(
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    link: Link,
    *,
    nsfw: bool,
) -> None:
    """r2's marknsfw: the author or an innkeeper marks a link NSFW, or takes the mark off.

    Every link in an NSFW community is NSFW, so there it stays on. An innkeeper's change goes in
    the moderation log.
    """
    is_author = link.author_id == actor.id
    if not is_author:
        require(actor, Action.MODERATE, community, standing)
    if not nsfw and community.nsfw:
        raise InaError.of(ErrorCode.COMMUNITY_NOTALLOWED, 'id', status_code=FORBIDDEN)
    await moderation.set_link_flags(link.id, nsfw=nsfw)
    if not is_author:
        action = 'marknsfw' if nsfw else 'unmarknsfw'
        await moderation.log(community.id, actor.id, action, link.fullname)


async def report(
    moderation: ModerationRepo,
    actor: Account,
    standing: Standing,
    community: Community,
    thing: Fullname,
    reason: str,
) -> None:
    if not can(actor, Action.VIEW, community, standing=standing):
        raise InaError.of(ErrorCode.COMMUNITY_NOTALLOWED, 'thing_id', status_code=FORBIDDEN)
    if len(reason) > MAX_REASON:
        raise InaError.of(ErrorCode.TOO_LONG, 'reason', max_length=MAX_REASON)
    await moderation.report(thing, actor.id, reason.strip())
