from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import msgspec
from sqlalchemy import delete, false, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import IntegrityError

from app.repos.base import Account, Member, NameTakenError, Prefs
from app.repos.pg.tables import accounts, comments, links, relations

if TYPE_CHECKING:
    from sqlalchemy import Row
    from sqlalchemy.ext.asyncio import AsyncSession

COLUMNS = (
    accounts.c.id,
    accounts.c.name,
    accounts.c.password_hash,
    accounts.c.email,
    accounts.c.email_verified,
    accounts.c.created,
    accounts.c.link_karma,
    accounts.c.comment_karma,
    accounts.c.prefs,
    accounts.c.deleted,
    accounts.c.suspended,
    accounts.c.in_timeout,
    accounts.c.inbox_count,
    accounts.c.session_version,
    accounts.c.modmsgtime,
    accounts.c.is_admin,
    accounts.c.timeout_expires,
)


def to_account(row: Row[*tuple[Any, ...]]) -> Account:
    return Account(
        id=row.id,
        name=row.name,
        password_hash=row.password_hash,
        email=row.email,
        email_verified=row.email_verified,
        created=row.created,
        link_karma=row.link_karma,
        comment_karma=row.comment_karma,
        prefs=msgspec.convert(row.prefs, Prefs),
        deleted=row.deleted,
        suspended=row.suspended,
        # A timeout ends by itself; nothing has to come along and lift it.
        in_timeout=row.in_timeout
        and (row.timeout_expires is None or row.timeout_expires > datetime.now(UTC)),
        inbox_count=row.inbox_count,
        session_version=row.session_version,
        modmsgtime=row.modmsgtime,
        is_admin=row.is_admin,
        timeout_expires=row.timeout_expires,
    )


class PgAccountRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_id(self, account_id: int) -> Account | None:
        row = (
            await self.session.execute(select(*COLUMNS).where(accounts.c.id == account_id))
        ).first()
        return to_account(row) if row else None

    async def by_name(self, name: str) -> Account | None:
        row = (await self.session.execute(select(*COLUMNS).where(accounts.c.name == name))).first()
        return to_account(row) if row else None

    async def create(self, *, name: str, password_hash: str, email: str | None) -> Account:
        statement = (
            insert(accounts)
            .values(name=name, password_hash=password_hash, email=email)
            .returning(*COLUMNS)
        )
        try:
            async with self.session.begin_nested():
                row = (await self.session.execute(statement)).one()
        except IntegrityError as error:
            raise NameTakenError(name) from error
        return to_account(row)

    async def recount_karma(self, account_id: int) -> None:
        # As r2 counted it: each item's score less its author's own vote, and nothing for
        # self posts.
        link_karma = (
            select(func.coalesce(func.sum(links.c.score - 1), 0))
            .where(
                links.c.author_id == account_id,
                links.c.is_self == false(),
                links.c.deleted == false(),
            )
            .scalar_subquery()
        )
        comment_karma = (
            select(func.coalesce(func.sum(comments.c.score - 1), 0))
            .where(comments.c.author_id == account_id, comments.c.deleted == false())
            .scalar_subquery()
        )
        await self.session.execute(
            update(accounts)
            .where(accounts.c.id == account_id)
            .values(link_karma=link_karma, comment_karma=comment_karma)
        )

    async def set_password(self, account_id: int, password_hash: str) -> int:
        statement = (
            update(accounts)
            .where(accounts.c.id == account_id)
            .values(password_hash=password_hash, session_version=accounts.c.session_version + 1)
            .returning(accounts.c.session_version)
        )
        return int((await self.session.execute(statement)).scalar_one())

    async def rehash_password(self, account_id: int, password_hash: str) -> None:
        await self.session.execute(
            update(accounts).where(accounts.c.id == account_id).values(password_hash=password_hash)
        )

    async def set_email(self, account_id: int, email: str | None) -> None:
        await self.session.execute(
            update(accounts)
            .where(accounts.c.id == account_id)
            .values(email=email, email_verified=False)
        )

    async def set_prefs(self, account_id: int, prefs: Prefs) -> None:
        await self.session.execute(
            update(accounts)
            .where(accounts.c.id == account_id)
            .values(prefs=msgspec.to_builtins(prefs))
        )

    async def set_admin(self, account_id: int, *, admin: bool) -> None:
        await self.session.execute(
            update(accounts).where(accounts.c.id == account_id).values(is_admin=admin)
        )

    async def suspend(self, account_id: int, *, until: datetime | None) -> None:
        if until is None:
            values: dict[str, Any] = {'suspended': True, 'in_timeout': False}
        else:
            values = {'in_timeout': True, 'timeout_expires': until}
        await self.session.execute(
            update(accounts).where(accounts.c.id == account_id).values(**values)
        )

    async def unsuspend(self, account_id: int) -> None:
        await self.session.execute(
            update(accounts)
            .where(accounts.c.id == account_id)
            .values(suspended=False, in_timeout=False, timeout_expires=None)
        )

    async def newest(self, *, limit: int, before_id: int | None = None) -> list[Account]:
        statement = select(*COLUMNS).order_by(accounts.c.id.desc()).limit(limit)
        if before_id is not None:
            statement = statement.where(accounts.c.id < before_id)
        return [to_account(row) for row in await self.session.execute(statement)]

    async def admins(self) -> list[str]:
        statement = select(accounts.c.name).where(accounts.c.is_admin).order_by(accounts.c.name)
        return list((await self.session.execute(statement)).scalars())

    async def bump_session_version(self, account_id: int) -> int:
        statement = (
            update(accounts)
            .where(accounts.c.id == account_id)
            .values(session_version=accounts.c.session_version + 1)
            .returning(accounts.c.session_version)
        )
        return int((await self.session.execute(statement)).scalar_one())

    async def set_inbox_count(self, account_id: int, count: int) -> None:
        await self.session.execute(
            update(accounts).where(accounts.c.id == account_id).values(inbox_count=count)
        )

    async def set_modmsgtime(self, account_id: int, when: datetime) -> None:
        await self.session.execute(
            update(accounts).where(accounts.c.id == account_id).values(modmsgtime=when)
        )

    async def relate(self, account_id: int, target_id: int, kind: str, *, note: str = '') -> bool:
        statement = (
            upsert(relations)
            .values(account_id=account_id, target_id=target_id, type=kind, note=note)
            .on_conflict_do_nothing()
            .returning(relations.c.target_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def unrelate(self, account_id: int, target_id: int, kind: str) -> bool:
        statement = (
            delete(relations)
            .where(
                relations.c.account_id == account_id,
                relations.c.target_id == target_id,
                relations.c.type == kind,
            )
            .returning(relations.c.target_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def related(self, account_id: int, kind: str) -> list[Member]:
        statement = (
            select(
                relations.c.target_id.label('account_id'),
                accounts.c.name,
                relations.c.created,
                relations.c.note,
            )
            .join(accounts, accounts.c.id == relations.c.target_id)
            .where(relations.c.account_id == account_id, relations.c.type == kind)
            .order_by(func.lower(accounts.c.name))
        )
        return [Member(**row._asdict()) for row in await self.session.execute(statement)]

    async def related_among(self, account_id: int, kind: str, target_ids: list[int]) -> set[int]:
        if not target_ids:
            return set()
        statement = select(relations.c.target_id).where(
            relations.c.account_id == account_id,
            relations.c.type == kind,
            relations.c.target_id.in_(target_ids),
        )
        return set((await self.session.execute(statement)).scalars())

    async def related_ids(self, account_id: int, kind: str) -> list[int]:
        statement = select(relations.c.target_id).where(
            relations.c.account_id == account_id, relations.c.type == kind
        )
        return list((await self.session.execute(statement)).scalars())
