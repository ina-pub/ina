"""API apps and their OAuth2 grants, after r2's OAuth2 provider and RFCs 6749, 7009 and 7636.

An app is registered by an account on ``/prefs/apps``. A web app runs on a server and keeps a
secret; an installed app (on a phone or a desktop) can't keep one and proves itself with PKCE
instead; a script keeps a secret and acts as the account that registered it.

Secrets and tokens are random and shown once; only their sha256 is stored. An access token
lasts an hour. A permanent grant also has a refresh token, which is replaced each time it is
used. An authorization code lives in Redis for ten minutes and is good once.

There is no password grant: an app never sees a password. A script asks for its owner's token
with its own id and secret instead (the client credentials grant).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import msgspec

from app.errors import ErrorCode, InaError
from app.repos.base import AppFields, AppKind
from app.text.urls import clean_url

if TYPE_CHECKING:
    from collections.abc import Mapping

    from redis.asyncio import Redis

    from app.repos.base import Account, AccountRepo, OAuthClient, OAuthRepo, OAuthToken

MAX_NAME = 100
MAX_DESCRIPTION = 500
NOT_FOUND = 404
LOOPBACK = frozenset({'localhost', '127.0.0.1', '::1'})
ACCESS_LIFETIME = timedelta(hours=1)
CODE_LIFETIME = timedelta(minutes=10)
DURATIONS = ('temporary', 'permanent')
# What a web app may do for itself, with no account: read what anyone logged out could.
APP_ONLY_SCOPES = frozenset({'read', 'wikiread'})
UNAUTHORIZED = 401


@dataclass(frozen=True, slots=True)
class Scope:
    id: str
    name: str
    description: str


# What an app may ask for; each API endpoint needs one of these. The ids are r2's, but for
# communities, so apps written against them read the same.
SCOPES = {
    scope.id: scope
    for scope in (
        Scope('identity', 'your account name', 'See your username and when you signed up.'),
        Scope('read', 'reading', 'Read posts, comments and communities as you see them.'),
        Scope('history', 'your history', 'See what you posted, saved, hid and voted on.'),
        Scope('vote', 'voting', 'Vote on posts and comments for you.'),
        Scope('submit', 'posting', 'Post links and comments as you.'),
        Scope('edit', 'editing', 'Edit and delete what you posted.'),
        Scope('save', 'saving', 'Save and unsave posts and comments for you.'),
        Scope('report', 'reporting and hiding', 'Report and hide posts for you.'),
        Scope('subscribe', 'subscriptions', 'Join and leave communities, and change your multis.'),
        Scope('mycommunities', 'your communities', "See which communities you're in and keep."),
        Scope('privatemessages', 'messages', 'Read and send your private messages.'),
        Scope('flair', 'your flair', "Choose your flair and your posts' flair."),
        Scope('modposts', 'moderating posts', 'Remove, approve, pin and lock posts as you.'),
        Scope('modconfig', 'community settings', 'Change the settings of communities you keep.'),
        Scope('modflair', 'managing flair', 'Manage flair in communities you keep.'),
        Scope('modlog', 'the moderation log', 'Read the moderation log of communities you keep.'),
        Scope(
            'modcontributors',
            'bans and members',
            'Ban, mute and approve people in communities you keep.',
        ),
        Scope('modothers', 'inviting innkeepers', 'Invite and remove other innkeepers.'),
        Scope('modself', 'your innkeeper roles', 'Accept invitations to keep, and step down.'),
        Scope('modwiki', 'managing wikis', 'Manage the wikis of communities you keep.'),
        Scope('wikiread', 'reading wikis', 'Read wiki pages.'),
        Scope('wikiedit', 'editing wikis', 'Edit wiki pages as you.'),
        Scope('setpermissions', 'innkeeper permissions', 'Change what other innkeepers may do.'),
    )
}


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _redirect_uri(raw: str, kind: AppKind) -> str:
    """Where the app is sent back to.

    Installed apps may use their own scheme; a plain http address is only for the app's own
    machine.
    """
    uri = raw.strip()
    if not uri:
        raise InaError.of(ErrorCode.NO_URL, 'redirect_uri')
    try:
        parts = urlsplit(uri)
        host = parts.hostname
    except ValueError:
        raise InaError.of(ErrorCode.BAD_URL, 'redirect_uri') from None
    web = parts.scheme in {'http', 'https'}
    valid = (
        bool(parts.scheme)
        and not parts.fragment
        and (web or kind is AppKind.INSTALLED)
        and (not web or bool(host))
        and (parts.scheme != 'http' or host in LOOPBACK)
    )
    if not valid:
        raise InaError.of(ErrorCode.BAD_URL, 'redirect_uri')
    return uri


def app_fields(
    kind: AppKind, *, name: str, description: str, about_url: str, redirect_uri: str
) -> AppFields:
    name = name.strip()
    if not name:
        raise InaError.of(ErrorCode.NO_TEXT, 'name')
    if len(name) > MAX_NAME:
        raise InaError.of(ErrorCode.TOO_LONG, 'name', max_length=MAX_NAME)
    description = description.strip()
    if len(description) > MAX_DESCRIPTION:
        raise InaError.of(ErrorCode.TOO_LONG, 'description', max_length=MAX_DESCRIPTION)
    about = ''
    if about_url.strip():
        about = clean_url(about_url) or ''
        if not about:
            raise InaError.of(ErrorCode.BAD_URL, 'about_url')
    return AppFields(
        name=name,
        description=description,
        about_url=about,
        redirect_uri=_redirect_uri(redirect_uri, kind),
    )


async def register(
    apps: OAuthRepo, owner: Account, kind: AppKind, fields: AppFields
) -> tuple[OAuthClient, str | None]:
    """The new app, and its secret if it keeps one."""
    secret = None if kind is AppKind.INSTALLED else secrets.token_urlsafe(32)
    client = await apps.add_client(
        secrets.token_urlsafe(16), owner.id, kind, fields, digest(secret) if secret else None
    )
    return client, secret


async def owned(apps: OAuthRepo, owner: Account, client_id: str) -> OAuthClient:
    client = await apps.client(client_id)
    if client is None or client.owner_id != owner.id:
        raise InaError.of(ErrorCode.NOT_FOUND, 'client_id', status_code=NOT_FOUND)
    return client


async def new_secret(apps: OAuthRepo, client: OAuthClient) -> str:
    """Replace the app's secret; the old one stops working at once."""
    if client.kind is AppKind.INSTALLED:
        raise InaError.of(ErrorCode.INVALID_OPTION, 'client_id')
    secret = secrets.token_urlsafe(32)
    await apps.set_secret(client.id, digest(secret))
    return secret


class Problem(StrEnum):
    """RFC 6749's error codes, and RFC 6750's ``invalid_token``."""

    ACCESS_DENIED = 'access_denied'
    INVALID_CLIENT = 'invalid_client'
    INVALID_GRANT = 'invalid_grant'
    INVALID_REQUEST = 'invalid_request'
    INVALID_SCOPE = 'invalid_scope'
    INVALID_TOKEN = 'invalid_token'  # noqa: S105
    INSUFFICIENT_SCOPE = 'insufficient_scope'
    UNAUTHORIZED_CLIENT = 'unauthorized_client'
    UNSUPPORTED_GRANT_TYPE = 'unsupported_grant_type'
    UNSUPPORTED_RESPONSE_TYPE = 'unsupported_response_type'


class OAuthError(Exception):
    """An error as RFC 6749 sends it."""

    def __init__(self, error: Problem, description: str = '', status_code: int = 400) -> None:
        super().__init__(error)
        self.error = error
        self.description = description
        self.status_code = status_code
        # Set when the error goes back to the app on its redirect URI, with the request's state.
        self.redirect_uri: str | None = None
        self.state = ''
        # The scope an endpoint needs, when a token lacks it.
        self.scope = ''

    def to_json(self) -> dict[str, str]:
        body = {'error': str(self.error)}
        if self.description:
            body['error_description'] = self.description
        return body


class UnknownAppError(Exception):
    """An authorization request naming no app, or the wrong redirect URI for one.

    It can't safely be sent back to the app, so the person sees why instead.
    """


def parse_scopes(raw: str) -> frozenset[str]:
    """Scopes as OAuth writes them, space-separated; r2 also took commas, and ``*`` for all."""
    asked = frozenset(raw.replace(',', ' ').split())
    if '*' in asked:
        return frozenset(SCOPES)
    if not asked or not asked <= SCOPES.keys():
        raise OAuthError(Problem.INVALID_SCOPE)
    return asked


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizeRequest:
    client: OAuthClient
    redirect_uri: str
    scopes: frozenset[str]
    state: str
    duration: str
    code_challenge: str | None


async def check_request(apps: OAuthRepo, params: Mapping[str, str]) -> AuthorizeRequest:
    """The authorization request in ``params``, checked.

    Raises UnknownAppError while the redirect URI isn't yet known to be the app's, and OAuthError
    once it is, to be sent back to the app.
    """
    client = await apps.client(params.get('client_id', ''))
    if client is None:
        msg = "that app isn't registered here"
        raise UnknownAppError(msg)
    if params.get('redirect_uri', client.redirect_uri) != client.redirect_uri:
        msg = "that redirect URI isn't the one the app registered"
        raise UnknownAppError(msg)
    try:
        return _checked(client, params)
    except OAuthError as error:
        error.redirect_uri, error.state = client.redirect_uri, params.get('state', '')
        raise


def _checked(client: OAuthClient, params: Mapping[str, str]) -> AuthorizeRequest:
    if params.get('response_type') != 'code':
        raise OAuthError(Problem.UNSUPPORTED_RESPONSE_TYPE)
    duration = params.get('duration', 'temporary')
    if duration not in DURATIONS:
        raise OAuthError(Problem.INVALID_REQUEST, 'duration is temporary or permanent')
    challenge = params.get('code_challenge') or None
    if challenge is not None and params.get('code_challenge_method') != 'S256':
        raise OAuthError(Problem.INVALID_REQUEST, 'code_challenge_method must be S256')
    if challenge is None and client.kind is AppKind.INSTALLED:
        raise OAuthError(Problem.INVALID_REQUEST, 'installed apps send a code_challenge')
    return AuthorizeRequest(
        client=client,
        redirect_uri=client.redirect_uri,
        scopes=parse_scopes(params.get('scope', '')),
        state=params.get('state', ''),
        duration=duration,
        code_challenge=challenge,
    )


class Code(msgspec.Struct, frozen=True):
    client_id: str
    account_id: int
    scopes: list[str]
    redirect_uri: str
    duration: str
    code_challenge: str | None


def _code_key(code: str) -> str:
    return f'oauth:code:{digest(code)}'


async def issue_code(redis: Redis, request: AuthorizeRequest, account: Account) -> str:
    code = secrets.token_urlsafe(32)
    grant = Code(
        client_id=request.client.id,
        account_id=account.id,
        scopes=sorted(request.scopes),
        redirect_uri=request.redirect_uri,
        duration=request.duration,
        code_challenge=request.code_challenge,
    )
    lifetime = int(CODE_LIFETIME.total_seconds())
    await redis.set(_code_key(code), msgspec.json.encode(grant), ex=lifetime)
    return code


@dataclass(frozen=True, slots=True, kw_only=True)
class Issued:
    """A token response's fields."""

    access_token: str
    scopes: frozenset[str]
    refresh_token: str | None = None

    def to_json(self) -> dict[str, str | int]:
        body: dict[str, str | int] = {
            'access_token': self.access_token,
            'token_type': 'bearer',
            'expires_in': int(ACCESS_LIFETIME.total_seconds()),
            'scope': ' '.join(sorted(self.scopes)),
        }
        if self.refresh_token:
            body['refresh_token'] = self.refresh_token
        return body


async def _issue(
    apps: OAuthRepo,
    client: OAuthClient,
    account_id: int | None,
    scopes: frozenset[str],
    *,
    refreshable: bool,
) -> Issued:
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32) if refreshable else None
    await apps.add_token(
        client.id,
        account_id,
        scopes,
        access_hash=digest(access),
        access_expires=datetime.now(UTC) + ACCESS_LIFETIME,
        refresh_hash=digest(refresh) if refresh else None,
    )
    return Issued(access_token=access, scopes=scopes, refresh_token=refresh)


async def authenticate(apps: OAuthRepo, client_id: str, secret: str) -> OAuthClient:
    """The app asking for a token. An installed app has no secret and sends an empty one."""
    client = await apps.client(client_id) if client_id else None
    if client is None:
        raise OAuthError(Problem.INVALID_CLIENT, status_code=UNAUTHORIZED)
    if client.secret_hash is None:
        valid = not secret
    else:
        valid = secrets.compare_digest(digest(secret), client.secret_hash)
    if not valid:
        raise OAuthError(Problem.INVALID_CLIENT, status_code=UNAUTHORIZED)
    return client


def _challenge(verifier: str) -> str:
    hashed = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(hashed).rstrip(b'=').decode()


async def exchange_code(
    redis: Redis,
    apps: OAuthRepo,
    client: OAuthClient,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> Issued:
    raw = await redis.getdel(_code_key(code)) if code else None
    if raw is None:
        raise OAuthError(Problem.INVALID_GRANT)
    grant = msgspec.json.decode(raw, type=Code)
    # The app has one redirect URI, so one sent here can only be checked against it.
    if grant.client_id != client.id or redirect_uri not in {'', grant.redirect_uri}:
        raise OAuthError(Problem.INVALID_GRANT)
    if grant.code_challenge is not None and not secrets.compare_digest(
        _challenge(code_verifier), grant.code_challenge
    ):
        raise OAuthError(Problem.INVALID_GRANT, 'the code_verifier does not match')
    return await _issue(
        apps,
        client,
        grant.account_id,
        frozenset(grant.scopes),
        refreshable=grant.duration == 'permanent',
    )


async def refresh(apps: OAuthRepo, client: OAuthClient, refresh_token: str) -> Issued:
    """A new access token for a permanent grant, and a new refresh token in place of the old."""
    token = await apps.by_refresh(digest(refresh_token)) if refresh_token else None
    if token is None or token.client_id != client.id:
        raise OAuthError(Problem.INVALID_GRANT)
    access, renewed = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await apps.rotate(
        token.id,
        access_hash=digest(access),
        access_expires=datetime.now(UTC) + ACCESS_LIFETIME,
        refresh_hash=digest(renewed),
    )
    return Issued(access_token=access, scopes=token.scopes, refresh_token=renewed)


async def client_credentials(apps: OAuthRepo, client: OAuthClient, scope: str) -> Issued:
    """A script acts as its owner; a web app acts for itself, and may only read."""
    scopes = parse_scopes(scope)
    if client.kind is AppKind.SCRIPT:
        return await _issue(apps, client, client.owner_id, scopes, refreshable=False)
    if client.kind is AppKind.INSTALLED:
        raise OAuthError(Problem.UNAUTHORIZED_CLIENT)
    if not scopes <= APP_ONLY_SCOPES:
        raise OAuthError(Problem.INVALID_SCOPE, 'an app acting for itself may only read')
    return await _issue(apps, client, None, scopes, refreshable=False)


async def revoke(apps: OAuthRepo, client: OAuthClient, token: str) -> None:
    """End the grant behind an access or refresh token. Unknown tokens are fine (RFC 7009)."""
    hashed = digest(token)
    found = await apps.by_access(hashed) or await apps.by_refresh(hashed)
    if found is not None and found.client_id == client.id:
        await apps.delete_token(found.id)


@dataclass(frozen=True, slots=True, kw_only=True)
class Bearer:
    """A request made with an access token: the grant, and the account it acts for."""

    token: OAuthToken
    account: Account | None


async def bearer(apps: OAuthRepo, accounts: AccountRepo, access_token: str) -> Bearer:
    token = await apps.by_access(digest(access_token))
    if token is None or token.access_expires <= datetime.now(UTC):
        raise OAuthError(Problem.INVALID_TOKEN, status_code=UNAUTHORIZED)
    account = None
    if token.account_id is not None:
        account = await accounts.by_id(token.account_id)
        if account is None or account.deleted or account.suspended:
            raise OAuthError(Problem.INVALID_TOKEN, status_code=UNAUTHORIZED)
    return Bearer(token=token, account=account)
