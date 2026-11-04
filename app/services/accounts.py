"""Registration, login, password and email changes, and password reset tokens."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.errors import ErrorCode, FieldError, InaError
from app.repos.base import NameTakenError

if TYPE_CHECKING:
    from app.repos.base import Account, AccountRepo
    from app.services.passwords import Passwords
    from app.settings import Settings

MIN_NAME_LENGTH = 3
MAX_NAME_LENGTH = 20
MIN_PASSWORD_LENGTH = 6
NAME = re.compile(r'\A[A-Za-z0-9_-]+\Z')
EMAIL = re.compile(r'\A[^\s@]+@[^\s@]+\.[^\s@]+\Z')
RESET_TOKEN_AGE = 12 * 60 * 60
# Accounts the site acts as, which nobody may register, and r2's name for Bouncer, so nobody
# can pass as it.
BOUNCER = 'Bouncer'
RESERVED = frozenset({BOUNCER.lower(), 'automoderator'})


def name_errors(name: str) -> list[FieldError]:
    if not MIN_NAME_LENGTH <= len(name) <= MAX_NAME_LENGTH:
        limits = {'min': MIN_NAME_LENGTH, 'max': MAX_NAME_LENGTH}
        return [FieldError(ErrorCode.USERNAME_TOO_SHORT, 'user', limits)]
    if not NAME.match(name):
        return [FieldError(ErrorCode.USERNAME_INVALID_CHARACTERS, 'user')]
    if name.lower() in RESERVED:
        return [FieldError(ErrorCode.USERNAME_TAKEN, 'user')]
    return []


def new_password_errors(password: str, verify: str, *, fields: tuple[str, str]) -> list[FieldError]:
    if len(password) < MIN_PASSWORD_LENGTH:
        limits = {'chars': MIN_PASSWORD_LENGTH}
        return [FieldError(ErrorCode.SHORT_PASSWORD, fields[0], limits)]
    if password != verify:
        return [FieldError(ErrorCode.BAD_PASSWORD_MATCH, fields[1])]
    return []


def clean_email(email: str) -> str | None:
    """Strip an optional email; raise BAD_EMAIL if one was given and is not an address."""
    email = email.strip()
    if not email:
        return None
    if not EMAIL.match(email):
        raise InaError.of(ErrorCode.BAD_EMAIL, 'email')
    return email


async def register(
    accounts: AccountRepo,
    passwords: Passwords,
    *,
    name: str,
    password: str,
    verify: str,
    email: str,
) -> Account:
    errors = name_errors(name) + new_password_errors(password, verify, fields=('passwd', 'passwd2'))
    cleaned_email: str | None = None
    try:
        cleaned_email = clean_email(email)
    except InaError as error:
        errors.extend(error.errors)
    if not errors and (existing := await accounts.by_name(name)):
        taken = ErrorCode.USERNAME_TAKEN_DEL if existing.deleted else ErrorCode.USERNAME_TAKEN
        errors.append(FieldError(taken, 'user'))
    if errors:
        raise InaError(*errors)
    try:
        return await accounts.create(
            name=name, password_hash=await passwords.hash(password), email=cleaned_email
        )
    except NameTakenError:
        raise InaError.of(ErrorCode.USERNAME_TAKEN, 'user') from None


async def authenticate(
    accounts: AccountRepo, passwords: Passwords, *, name: str, password: str
) -> Account:
    """The account for a name and password; WRONG_PASSWORD covers every failure, as on r2."""
    account = await accounts.by_name(name) if name else None
    if (
        account is None
        or account.deleted
        or account.suspended
        or not await passwords.verify(account.password_hash, password)
    ):
        raise InaError.of(ErrorCode.WRONG_PASSWORD, 'passwd')
    if passwords.needs_rehash(account.password_hash):
        await accounts.rehash_password(account.id, await passwords.hash(password))
    return account


async def check_password(passwords: Passwords, account: Account, password: str) -> None:
    if not await passwords.verify(account.password_hash, password):
        raise InaError.of(ErrorCode.WRONG_PASSWORD, 'curpass')


async def change_password(
    accounts: AccountRepo,
    passwords: Passwords,
    account: Account,
    *,
    current: str,
    password: str,
    verify: str,
) -> int:
    """Set a new password and sign out every session; return the new session version."""
    await check_password(passwords, account, current)
    if errors := new_password_errors(password, verify, fields=('newpass', 'verpass')):
        raise InaError(*errors)
    return await accounts.set_password(account.id, await passwords.hash(password))


async def change_email(
    accounts: AccountRepo, passwords: Passwords, account: Account, *, current: str, email: str
) -> None:
    await check_password(passwords, account, current)
    await accounts.set_email(account.id, clean_email(email))


def _reset_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt='password-reset')


def _fingerprint(account: Account) -> str:
    """Changes whenever the password does, so a reset token works only once."""
    material = f'{account.password_hash}:{account.session_version}'.encode()
    return hashlib.sha256(material).hexdigest()[:16]


def reset_token(settings: Settings, account: Account) -> str:
    return _reset_serializer(settings).dumps([account.id, _fingerprint(account)])


async def account_for_reset(
    settings: Settings, accounts: AccountRepo, token: str
) -> Account | None:
    try:
        account_id, fingerprint = _reset_serializer(settings).loads(token, max_age=RESET_TOKEN_AGE)
    except (BadSignature, ValueError, TypeError):
        return None
    account = await accounts.by_id(int(account_id))
    if account is None or account.deleted or account.suspended:
        return None
    return account if _fingerprint(account) == fingerprint else None


async def account_for_reset_request(accounts: AccountRepo, name: str) -> Account:
    """The account a reset email should go to, or the error r2 showed for that name."""
    if not name:
        raise InaError.of(ErrorCode.NO_USER, 'name')
    account = await accounts.by_name(name)
    if account is None or account.deleted:
        raise InaError.of(ErrorCode.USER_DOESNT_EXIST, 'name')
    if not account.email:
        raise InaError.of(ErrorCode.NO_EMAIL_FOR_USER, 'name')
    return account


async def reset_password(
    settings: Settings,
    accounts: AccountRepo,
    passwords: Passwords,
    *,
    token: str,
    password: str,
    verify: str,
) -> Account:
    account = await account_for_reset(settings, accounts, token)
    if account is None:
        raise InaError.of(ErrorCode.EXPIRED, 'key')
    if errors := new_password_errors(password, verify, fields=('passwd', 'passwd2')):
        raise InaError(*errors)
    await accounts.set_password(account.id, await passwords.hash(password))
    return account
