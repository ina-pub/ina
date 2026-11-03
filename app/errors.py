"""Error codes, as reddit's API names them but for communities, and ina's messages."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from litestar.status_codes import HTTP_400_BAD_REQUEST

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.jsontypes import JSON


class ErrorCode(StrEnum):
    ALREADY_MODERATOR = 'ALREADY_MODERATOR'
    ALREADY_SUB = 'ALREADY_SUB'
    BAD_COLOR = 'BAD_COLOR'
    BAD_CSRF = 'BAD_CSRF'
    BAD_CSS_NAME = 'BAD_CSS_NAME'
    BAD_EMAIL = 'BAD_EMAIL'
    BAD_FLAIR_TARGET = 'BAD_FLAIR_TARGET'
    BAD_MULTI_NAME = 'BAD_MULTI_NAME'
    BAD_NUMBER = 'BAD_NUMBER'
    BAD_PASSWORD = 'BAD_PASSWORD'
    BAD_PASSWORD_MATCH = 'BAD_PASSWORD_MATCH'
    BAD_COMMUNITY_NAME = 'BAD_COMMUNITY_NAME'
    BAD_URL = 'BAD_URL'
    BAD_USERNAME = 'BAD_USERNAME'
    BANNED_FROM_COMMUNITY = 'BANNED_FROM_COMMUNITY'
    CANT_CREATE_COMMUNITY = 'CANT_CREATE_COMMUNITY'
    CANT_RESTRICT_MODERATOR = 'CANT_RESTRICT_MODERATOR'
    CONTENT_LENGTH_ERROR = 'CONTENT_LENGTH_ERROR'
    ADMIN_MODE_REQUIRED = 'ADMIN_MODE_REQUIRED'
    ADMIN_REMOVED = 'ADMIN_REMOVED'
    CANT_RESTRICT_ADMIN = 'CANT_RESTRICT_ADMIN'
    IN_TIMEOUT = 'IN_TIMEOUT'
    COMMUNITY_BANNED = 'COMMUNITY_BANNED'
    DELETED_COMMENT = 'DELETED_COMMENT'
    EDIT_CONFLICT = 'EDIT_CONFLICT'
    EXPIRED = 'EXPIRED'
    IMAGE_ERROR = 'IMAGE_ERROR'
    INVALID_OPTION = 'INVALID_OPTION'
    INVALID_PAGE_NAME = 'INVALID_PAGE_NAME'
    INVALID_PERMISSIONS = 'INVALID_PERMISSIONS'
    INVALID_PERMLEVEL = 'INVALID_PERMLEVEL'
    INVALID_REVISION = 'INVALID_REVISION'
    JSON_PARSE_ERROR = 'JSON_PARSE_ERROR'
    LOGGED_IN = 'LOGGED_IN'
    MAY_NOT_REVISE = 'MAY_NOT_REVISE'
    MAY_NOT_VIEW = 'MAY_NOT_VIEW'
    MOD_REQUIRED = 'MOD_REQUIRED'
    MULTI_CANNOT_EDIT = 'MULTI_CANNOT_EDIT'
    MULTI_EXISTS = 'MULTI_EXISTS'
    MULTI_NOT_FOUND = 'MULTI_NOT_FOUND'
    MULTI_TOO_MANY_COMMUNITIES = 'MULTI_TOO_MANY_COMMUNITIES'
    NOT_AUTHOR = 'NOT_AUTHOR'
    NOT_FOUND = 'NOT_FOUND'
    NO_EMAIL = 'NO_EMAIL'
    NO_EMAIL_FOR_USER = 'NO_EMAIL_FOR_USER'
    NO_INVITE_FOUND = 'NO_INVITE_FOUND'
    NO_LINKS = 'NO_LINKS'
    NO_SELFS = 'NO_SELFS'
    NO_SUBJECT = 'NO_SUBJECT'
    NO_TEXT = 'NO_TEXT'
    NO_THING_ID = 'NO_THING_ID'
    NO_URL = 'NO_URL'
    NO_USER = 'NO_USER'
    PAGE_CREATED_ELSEWHERE = 'PAGE_CREATED_ELSEWHERE'
    PAGE_NOT_FOUND = 'PAGE_NOT_FOUND'
    PRIVATE_COMMUNITY = 'PRIVATE_COMMUNITY'
    QUARANTINED = 'QUARANTINED'
    RATELIMIT = 'RATELIMIT'
    RESTRICTED_PAGE = 'RESTRICTED_PAGE'
    SHORT_PASSWORD = 'SHORT_PASSWORD'
    SPECIAL_ERRORS = 'SPECIAL_ERRORS'
    COMMUNITY_EXISTS = 'COMMUNITY_EXISTS'
    COMMUNITY_NOEXIST = 'COMMUNITY_NOEXIST'
    COMMUNITY_NOTALLOWED = 'COMMUNITY_NOTALLOWED'
    COMMUNITY_REQUIRED = 'COMMUNITY_REQUIRED'
    THREAD_LOCKED = 'THREAD_LOCKED'
    TOO_LONG = 'TOO_LONG'
    TOO_MANY_FLAIR_TEMPLATES = 'TOO_MANY_FLAIR_TEMPLATES'
    TOO_MUCH_FLAIR_CSS = 'TOO_MUCH_FLAIR_CSS'
    TOO_OLD = 'TOO_OLD'
    USERNAME_INVALID_CHARACTERS = 'USERNAME_INVALID_CHARACTERS'
    USERNAME_TAKEN = 'USERNAME_TAKEN'
    USERNAME_TAKEN_DEL = 'USERNAME_TAKEN_DEL'
    USERNAME_TOO_SHORT = 'USERNAME_TOO_SHORT'
    USER_DOESNT_EXIST = 'USER_DOESNT_EXIST'
    USER_MUTED = 'USER_MUTED'
    USER_REQUIRED = 'USER_REQUIRED'
    WIKI_DISABLED = 'WIKI_DISABLED'
    WRONG_PASSWORD = 'WRONG_PASSWORD'


MESSAGES: Mapping[ErrorCode, str] = MappingProxyType(
    {
        ErrorCode.ALREADY_MODERATOR: 'they moderate here already',
        ErrorCode.ALREADY_SUB: 'someone already posted this link here',
        ErrorCode.BANNED_FROM_COMMUNITY: 'that account is banned here',
        ErrorCode.BAD_CSRF: 'this form is out of date; reload the page and try again',
        ErrorCode.BAD_EMAIL: "that doesn't look like an email address",
        ErrorCode.BAD_NUMBER: 'pick a number from {range}',
        ErrorCode.BAD_PASSWORD: "that password won't do; pick another",
        ErrorCode.BAD_PASSWORD_MATCH: "the two passwords aren't the same",
        ErrorCode.BAD_COMMUNITY_NAME: "that can't be a community's name",
        ErrorCode.BAD_URL: "that doesn't look like a link",
        ErrorCode.BAD_USERNAME: "that can't be a username",
        ErrorCode.CANT_CREATE_COMMUNITY: (
            'new accounts, and accounts with little karma, cannot start communities yet. '
            'ask the people who run this site if you need an exception.'
        ),
        ErrorCode.DELETED_COMMENT: 'that was deleted',
        ErrorCode.CANT_RESTRICT_MODERATOR: 'innkeepers are exempt from that',
        ErrorCode.EXPIRED: 'you were logged out; log in again',
        ErrorCode.INVALID_OPTION: "that isn't one of the choices",
        ErrorCode.INVALID_PERMISSIONS: "those permissions don't make sense",
        ErrorCode.LOGGED_IN: "you're logged in already",
        ErrorCode.MOD_REQUIRED: 'only innkeepers can do that',
        ErrorCode.NOT_AUTHOR: 'only the author can do that',
        ErrorCode.NO_EMAIL: 'an email address is needed',
        ErrorCode.NO_EMAIL_FOR_USER: 'that account has no email address',
        ErrorCode.NO_INVITE_FOUND: "you haven't been invited to moderate here",
        ErrorCode.NO_LINKS: 'this community takes text posts only',
        ErrorCode.NO_SELFS: 'this community takes links only',
        ErrorCode.NO_SUBJECT: 'a subject is needed',
        ErrorCode.NO_TEXT: "this can't be empty",
        ErrorCode.NO_URL: 'a link is needed',
        ErrorCode.NO_USER: 'a username is needed',
        ErrorCode.NO_THING_ID: "that post, comment or message isn't there",
        ErrorCode.NOT_FOUND: "there's nothing at this address",
        ErrorCode.USER_MUTED: "you're muted from this community's innkeepers for now",
        ErrorCode.PRIVATE_COMMUNITY: 'this community is private; only its members can see it',
        ErrorCode.QUARANTINED: 'this community is quarantined; opt in on its page to see it',
        ErrorCode.RATELIMIT: 'slow down a little; try again in {time}',
        ErrorCode.SHORT_PASSWORD: 'passwords need at least {chars} characters',
        ErrorCode.COMMUNITY_EXISTS: 'a community with that name exists',
        ErrorCode.COMMUNITY_NOEXIST: 'there is no community by that name',
        ErrorCode.SPECIAL_ERRORS: 'the rules have a mistake: {error}',
        ErrorCode.COMMUNITY_NOTALLOWED: "you can't do that in this community",
        ErrorCode.COMMUNITY_REQUIRED: 'choose a community',
        ErrorCode.THREAD_LOCKED: 'this thread is locked',
        ErrorCode.TOO_LONG: 'keep it under {max_length} characters',
        ErrorCode.TOO_OLD: 'this is archived; it takes no more votes or replies',
        ErrorCode.USER_DOESNT_EXIST: 'there is no account by that name',
        ErrorCode.USER_REQUIRED: 'log in first',
        ErrorCode.USERNAME_INVALID_CHARACTERS: 'usernames use letters, digits, "-" and "_" only',
        ErrorCode.USERNAME_TAKEN: 'someone has that username',
        ErrorCode.USERNAME_TAKEN_DEL: 'that username belonged to a deleted account',
        ErrorCode.USERNAME_TOO_SHORT: 'usernames are {min} to {max} characters long',
        ErrorCode.WRONG_PASSWORD: "that password isn't right",
        ErrorCode.BAD_COLOR: "that isn't a colour",
        ErrorCode.BAD_MULTI_NAME: "that can't be a multi's name",
        ErrorCode.JSON_PARSE_ERROR: "that JSON couldn't be read",
        ErrorCode.MULTI_CANNOT_EDIT: "that multi isn't yours to change",
        ErrorCode.MULTI_EXISTS: 'you have a multi with that name',
        ErrorCode.MULTI_NOT_FOUND: 'there is no multi by that name',
        ErrorCode.MULTI_TOO_MANY_COMMUNITIES: 'that multi is full',
        ErrorCode.IMAGE_ERROR: '{reason}',
        ErrorCode.BAD_CSS_NAME: 'css class names use letters, digits and "-" only',
        ErrorCode.BAD_FLAIR_TARGET: "that can't have flair",
        ErrorCode.TOO_MANY_FLAIR_TEMPLATES: 'there are as many flair templates as allowed',
        ErrorCode.TOO_MUCH_FLAIR_CSS: 'that flair has too many css classes',
        ErrorCode.CONTENT_LENGTH_ERROR: 'pages are limited to {max_length} bytes',
        ErrorCode.EDIT_CONFLICT: (
            'someone changed this page while you were editing; your version was not saved'
        ),
        ErrorCode.INVALID_PAGE_NAME: "that can't be a page's name",
        ErrorCode.INVALID_PERMLEVEL: "that isn't one of the permission levels",
        ErrorCode.INVALID_REVISION: 'there is no such revision',
        ErrorCode.MAY_NOT_REVISE: "you can't edit this page",
        ErrorCode.MAY_NOT_VIEW: "you can't see this page",
        ErrorCode.PAGE_CREATED_ELSEWHERE: "this page changes through the community's settings",
        ErrorCode.PAGE_NOT_FOUND: 'there is no page by that name',
        ErrorCode.RESTRICTED_PAGE: 'only innkeepers can edit this page',
        ErrorCode.WIKI_DISABLED: "this community's wiki is off",
        ErrorCode.ADMIN_MODE_REQUIRED: 'turn on admin mode first',
        ErrorCode.ADMIN_REMOVED: "this site's admins removed that, so only they can put it back",
        ErrorCode.COMMUNITY_BANNED: "in/{name} was banned by this site's admins",
        ErrorCode.CANT_RESTRICT_ADMIN: 'admins are exempt from that',
        ErrorCode.IN_TIMEOUT: (
            'your account is suspended until {until}: you can read, but not post, vote or send'
        ),
    }
)


@dataclass(frozen=True, slots=True)
class FieldError:
    """One error, reported as reddit's ``[code, message, field]`` triple."""

    code: ErrorCode
    field: str | None = None
    params: Mapping[str, str | int] = dataclasses.field(
        default_factory=lambda: MappingProxyType({})
    )

    @property
    def message(self) -> str:
        return MESSAGES[self.code].format(**self.params)

    def to_json(self) -> JSON:
        return [str(self.code), self.message, self.field]


class InaError(Exception):
    """Raised by services and controllers; rendered as an error page or an error list."""

    def __init__(self, *errors: FieldError, status_code: int = HTTP_400_BAD_REQUEST) -> None:
        super().__init__(', '.join(error.code for error in errors))
        self.errors = errors
        self.status_code = status_code

    @classmethod
    def of(
        cls,
        code: ErrorCode,
        field: str | None = None,
        *,
        status_code: int = HTTP_400_BAD_REQUEST,
        **params: str | int,
    ) -> InaError:
        return cls(FieldError(code, field, MappingProxyType(params)), status_code=status_code)
