"""Who may do what. Controllers ask here; templates receive the answers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.repos.base import ModPermission

if TYPE_CHECKING:
    from app.repos.base import Account, Community


@dataclass(frozen=True, slots=True)
class Standing:
    """An account's place in one community."""

    mod: ModPermission | None = None
    contributor: bool = False
    banned: bool = False
    # Acting as a site admin in admin mode, not as one of the community's innkeepers.
    admin: bool = False

    def has(self, permission: ModPermission) -> bool:
        return self.mod is not None and permission in self.mod

    @property
    def full_moderator(self) -> bool:
        return self.mod == ModPermission.ALL


# Anyone: logged out, or with no relation to the community.
NOBODY = Standing()


class Action(StrEnum):
    VIEW = 'view'
    SUBSCRIBE = 'subscribe'
    SUBMIT = 'submit'
    COMMENT = 'comment'
    VOTE = 'vote'
    # Remove, approve, spam, sticky, lock, distinguish, ignore reports: r2's 'posts'.
    MODERATE = 'moderate'
    # Bans and approved submitters: r2's 'access'.
    MANAGE_ACCESS = 'manage_access'
    # Wiki bans, wiki contributors, and page settings: r2's 'wiki'.
    MANAGE_WIKI = 'manage_wiki'
    # Invite moderators and change their permissions: full permissions only.
    MANAGE_MODERATORS = 'manage_moderators'
    # Modmail, and muting from it: r2's 'mail'.
    MANAGE_MAIL = 'manage_mail'
    CONFIGURE = 'configure'


PERMISSION = {
    Action.MODERATE: ModPermission.POSTS,
    Action.MANAGE_ACCESS: ModPermission.ACCESS,
    Action.MANAGE_WIKI: ModPermission.WIKI,
    Action.MANAGE_MAIL: ModPermission.MAIL,
    Action.CONFIGURE: ModPermission.CONFIG,
}


def can(
    actor: Account | None, action: Action, community: Community, *, standing: Standing = NOBODY
) -> bool:
    """Whether ``actor``, standing as given in ``community``, may do ``action`` there, as on r2."""
    if actor is not None and actor.is_admin:
        return True
    if community.banned:
        return False
    sees = community.type != 'private' or standing.mod is not None or standing.contributor
    if actor is None or action in {Action.VIEW, Action.SUBSCRIBE, Action.VOTE}:
        # Logged out, an account may only look; logged in, banned or not, it may also follow
        # and vote, as on r2.
        return sees and (actor is not None or action is Action.VIEW)
    if action is Action.MANAGE_MODERATORS:
        return standing.full_moderator
    if action in PERMISSION:
        return standing.has(PERMISSION[action])
    member = standing.mod is not None or standing.contributor
    open_to_all = action is Action.COMMENT or community.type == 'public'
    return sees and not standing.banned and (member or open_to_all)
