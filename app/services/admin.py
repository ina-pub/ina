"""What site admins do across the site, each logged in the admin log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import msgspec

from app.errors import ErrorCode, InaError

if TYPE_CHECKING:
    from app.repos.base import (
        Account,
        AccountRepo,
        AdminRepo,
        Community,
        CommunityRepo,
        ModerationRepo,
        RemovedThing,
    )

MAX_DAYS = 999
MAX_REASON = 300
NOT_FOUND = 404


async def target_account(accounts: AccountRepo, name: str) -> Account:
    account = await accounts.by_name(name.strip()) if name.strip() else None
    if account is None or account.deleted:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, 'name', status_code=NOT_FOUND)
    return account


def _reason(reason: str) -> str:
    reason = reason.strip()
    if len(reason) > MAX_REASON:
        raise InaError.of(ErrorCode.TOO_LONG, 'reason', max_length=MAX_REASON)
    return reason


async def suspend(
    accounts: AccountRepo, log: AdminRepo, admin: Account, name: str, days: str, reason: str
) -> Account:
    """Suspend an account.

    For ``days`` it may read but not post (a timeout); with no days it is suspended for good
    and signed out everywhere.
    """
    target = await target_account(accounts, name)
    if target.is_admin:
        raise InaError.of(ErrorCode.CANT_RESTRICT_ADMIN, 'name')
    reason = _reason(reason)
    until = None
    if days.strip():
        if not days.strip().isdigit() or not 1 <= int(days) <= MAX_DAYS:
            raise InaError.of(ErrorCode.BAD_NUMBER, 'days', range=f'1 to {MAX_DAYS}')
        until = datetime.now(UTC) + timedelta(days=int(days))
    await accounts.suspend(target.id, until=until)
    if until is None:
        await accounts.bump_session_version(target.id)
    details = f'{int(days)} days' if until else 'for good'
    await log.log(
        admin.id, 'suspend', target.fullname, f'{details}: {reason}' if reason else details
    )
    return target


async def unsuspend(
    accounts: AccountRepo, log: AdminRepo, admin: Account, name: str, reason: str
) -> Account:
    target = await target_account(accounts, name)
    await accounts.unsuspend(target.id)
    await log.log(admin.id, 'unsuspend', target.fullname, _reason(reason))
    return target


async def remove_all(
    accounts: AccountRepo,
    moderation: ModerationRepo,
    log: AdminRepo,
    admin: Account,
    name: str,
    reason: str,
) -> tuple[Account, list[RemovedThing]]:
    """Remove every link and comment an account posted, as r2's admins could with a ban."""
    target = await target_account(accounts, name)
    if target.is_admin:
        raise InaError.of(ErrorCode.CANT_RESTRICT_ADMIN, 'name')
    reason = _reason(reason)
    removed = await moderation.remove_all_by(target.id)
    count = f'{len(removed)} removed'
    await log.log(admin.id, 'removeall', target.fullname, f'{count}: {reason}' if reason else count)
    return target, removed


# What an admin may do to a community, and what each sets.
COMMUNITY_ACTIONS = ('ban', 'unban', 'quarantine', 'unquarantine', 'nsfw', 'sfw')


async def act_on_community(
    communities: CommunityRepo, log: AdminRepo, admin: Account, name: str, action: str, reason: str
) -> Community:
    """Ban or unban a community, quarantine it or not, or mark it NSFW or not."""
    community = await communities.by_name(name.strip()) if name.strip() else None
    if community is None:
        raise InaError.of(ErrorCode.COMMUNITY_NOEXIST, 'name', status_code=NOT_FOUND)
    if action not in COMMUNITY_ACTIONS:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'action')
    reason = _reason(reason)
    if action in {'ban', 'unban'}:
        await communities.set_flags(community.id, banned=action == 'ban')
    elif action in {'nsfw', 'sfw'}:
        await communities.set_flags(community.id, nsfw=action == 'nsfw')
    else:
        quarantined = msgspec.structs.replace(community.settings, quarantine=action == 'quarantine')
        await communities.set_settings(community.id, quarantined)
    await log.log(admin.id, action, community.fullname, reason)
    return community
