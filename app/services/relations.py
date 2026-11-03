"""Friends and blocks between accounts: r2's friend and enemy relations.

A friend's name stands out wherever they post, and /in/friends lists their links. A block keeps
the blocked account's messages, replies and mentions out of the blocker's inbox; they are still
sent, so the blocked account cannot tell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.errors import ErrorCode, InaError

if TYPE_CHECKING:
    from app.repos.base import Account, AccountRepo

FRIEND = 'friend'
ENEMY = 'enemy'
ACCOUNT_RELATIONS = (FRIEND, ENEMY)
MAX_NOTE = 300


async def target(accounts: AccountRepo, actor: Account, name: str) -> Account:
    found = await accounts.by_name(name.strip()) if name.strip() else None
    if found is None or found.deleted:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, 'name')
    if found.id == actor.id:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'name')
    return found


async def add(
    accounts: AccountRepo, actor: Account, name: str, kind: str, *, note: str = ''
) -> Account:
    """Befriend or block ``name``."""
    if kind not in ACCOUNT_RELATIONS:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'type')
    if len(note) > MAX_NOTE:
        raise InaError.of(ErrorCode.TOO_LONG, 'note', max_length=MAX_NOTE)
    found = await target(accounts, actor, name)
    await accounts.relate(actor.id, found.id, kind, note=note.strip())
    return found


async def remove(accounts: AccountRepo, actor: Account, name: str, kind: str) -> None:
    if kind not in ACCOUNT_RELATIONS:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'type')
    found = await accounts.by_name(name.strip()) if name.strip() else None
    if found is None:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, 'name')
    await accounts.unrelate(actor.id, found.id, kind)
