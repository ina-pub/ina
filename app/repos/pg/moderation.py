from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Integer,
    and_,
    delete,
    false,
    func,
    literal,
    or_,
    select,
    true,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as upsert

from app.ids import Fullname, Kind
from app.repos.base import Member, ModAction, ModPermission, Queue, QueueEntry, RemovedThing
from app.repos.pg.tables import (
    accounts,
    bans,
    comments,
    communities,
    contributors,
    links,
    moderator_invites,
    moderators,
    modlog,
    mutes,
    reports,
    wiki_bans,
    wiki_contributors,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import ColumnElement, Select, Table
    from sqlalchemy.ext.asyncio import AsyncSession

TABLES = {Kind.LINK: links, Kind.COMMENT: comments}


def _table(thing: Fullname) -> Table:
    if thing.kind not in TABLES:
        msg = f'not a link or comment: {thing}'
        raise ValueError(msg)
    return TABLES[thing.kind]


def _in_force(now: datetime) -> ColumnElement[bool]:
    return or_(bans.c.expires.is_(None), bans.c.expires > now)


class PgModerationRepo:
    async def mute(self, community_id: int, account_id: int, *, expires: datetime, by: int) -> None:
        statement = upsert(mutes).values(
            community_id=community_id, account_id=account_id, expires=expires, created_by=by
        )
        statement = statement.on_conflict_do_update(
            index_elements=[mutes.c.community_id, mutes.c.account_id],
            set_={'expires': expires, 'created_by': by, 'created': func.now()},
        )
        await self.session.execute(statement)

    async def unmute(self, community_id: int, account_id: int) -> bool:
        statement = (
            delete(mutes)
            .where(mutes.c.community_id == community_id, mutes.c.account_id == account_id)
            .returning(mutes.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def mutes(self, community_id: int) -> list[Member]:
        statement = (
            select(mutes.c.account_id, accounts.c.name, mutes.c.created, mutes.c.expires)
            .join(accounts, accounts.c.id == mutes.c.account_id)
            .where(mutes.c.community_id == community_id, mutes.c.expires > datetime.now(UTC))
            .order_by(mutes.c.created.desc())
        )
        return await self._members(statement)

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ban(
        self,
        community_id: int,
        account_id: int,
        *,
        note: str,
        message: str,
        expires: datetime | None,
        by: int,
    ) -> None:
        statement = upsert(bans).values(
            community_id=community_id,
            account_id=account_id,
            note=note,
            message=message,
            expires=expires,
            created_by=by,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[bans.c.community_id, bans.c.account_id],
            set_={'note': note, 'message': message, 'expires': expires, 'created_by': by},
        )
        await self.session.execute(statement)

    async def unban(self, community_id: int, account_id: int) -> bool:
        statement = (
            delete(bans)
            .where(bans.c.community_id == community_id, bans.c.account_id == account_id)
            .returning(bans.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def _members(self, statement: Select[Any]) -> list[Member]:
        return [Member(**row._asdict()) for row in await self.session.execute(statement)]

    async def bans(self, community_id: int) -> list[Member]:
        statement = (
            select(bans.c.account_id, accounts.c.name, bans.c.created, bans.c.note, bans.c.expires)
            .join(accounts, accounts.c.id == bans.c.account_id)
            .where(bans.c.community_id == community_id, _in_force(datetime.now(UTC)))
            .order_by(bans.c.created.desc())
        )
        return await self._members(statement)

    async def add_contributor(self, community_id: int, account_id: int, *, by: int) -> bool:
        statement = (
            upsert(contributors)
            .values(community_id=community_id, account_id=account_id, added_by=by)
            .on_conflict_do_nothing()
            .returning(contributors.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def remove_contributor(self, community_id: int, account_id: int) -> bool:
        statement = (
            delete(contributors)
            .where(
                contributors.c.community_id == community_id, contributors.c.account_id == account_id
            )
            .returning(contributors.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def contributors(self, community_id: int) -> list[Member]:
        statement = (
            select(contributors.c.account_id, accounts.c.name, contributors.c.created)
            .join(accounts, accounts.c.id == contributors.c.account_id)
            .where(contributors.c.community_id == community_id)
            .order_by(contributors.c.created.desc())
        )
        return await self._members(statement)

    async def wiki_standing(self, community_id: int, account_id: int) -> tuple[bool, bool]:
        contributor = (
            select(func.count())
            .where(
                wiki_contributors.c.community_id == community_id,
                wiki_contributors.c.account_id == account_id,
            )
            .scalar_subquery()
        )
        banned = (
            select(func.count())
            .where(
                wiki_bans.c.community_id == community_id,
                wiki_bans.c.account_id == account_id,
                or_(wiki_bans.c.expires.is_(None), wiki_bans.c.expires > datetime.now(UTC)),
            )
            .scalar_subquery()
        )
        row = (await self.session.execute(select(contributor, banned))).one()
        return bool(row[0]), bool(row[1])

    async def add_wiki_contributor(self, community_id: int, account_id: int, *, by: int) -> bool:
        statement = (
            upsert(wiki_contributors)
            .values(community_id=community_id, account_id=account_id, added_by=by)
            .on_conflict_do_nothing()
            .returning(wiki_contributors.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def remove_wiki_contributor(self, community_id: int, account_id: int) -> bool:
        statement = (
            delete(wiki_contributors)
            .where(
                wiki_contributors.c.community_id == community_id,
                wiki_contributors.c.account_id == account_id,
            )
            .returning(wiki_contributors.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def wiki_contributors(self, community_id: int) -> list[Member]:
        statement = (
            select(wiki_contributors.c.account_id, accounts.c.name, wiki_contributors.c.created)
            .join(accounts, accounts.c.id == wiki_contributors.c.account_id)
            .where(wiki_contributors.c.community_id == community_id)
            .order_by(wiki_contributors.c.created.desc())
        )
        return await self._members(statement)

    async def wiki_ban(
        self, community_id: int, account_id: int, *, note: str, expires: datetime | None, by: int
    ) -> None:
        statement = upsert(wiki_bans).values(
            community_id=community_id,
            account_id=account_id,
            note=note,
            expires=expires,
            created_by=by,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[wiki_bans.c.community_id, wiki_bans.c.account_id],
            set_={'note': note, 'expires': expires, 'created_by': by},
        )
        await self.session.execute(statement)

    async def wiki_unban(self, community_id: int, account_id: int) -> bool:
        statement = (
            delete(wiki_bans)
            .where(wiki_bans.c.community_id == community_id, wiki_bans.c.account_id == account_id)
            .returning(wiki_bans.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def wiki_bans(self, community_id: int) -> list[Member]:
        statement = (
            select(
                wiki_bans.c.account_id,
                accounts.c.name,
                wiki_bans.c.created,
                wiki_bans.c.note,
                wiki_bans.c.expires,
            )
            .join(accounts, accounts.c.id == wiki_bans.c.account_id)
            .where(
                wiki_bans.c.community_id == community_id,
                or_(wiki_bans.c.expires.is_(None), wiki_bans.c.expires > datetime.now(UTC)),
            )
            .order_by(wiki_bans.c.created.desc())
        )
        return await self._members(statement)

    async def invite(
        self, community_id: int, account_id: int, *, permissions: ModPermission, by: int
    ) -> None:
        statement = upsert(moderator_invites).values(
            community_id=community_id,
            account_id=account_id,
            permissions=int(permissions),
            invited_by=by,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[moderator_invites.c.community_id, moderator_invites.c.account_id],
            set_={'permissions': int(permissions), 'invited_by': by},
        )
        await self.session.execute(statement)

    async def take_invite(self, community_id: int, account_id: int) -> ModPermission | None:
        statement = (
            delete(moderator_invites)
            .where(
                moderator_invites.c.community_id == community_id,
                moderator_invites.c.account_id == account_id,
            )
            .returning(moderator_invites.c.permissions)
        )
        value = (await self.session.execute(statement)).scalar_one_or_none()
        return None if value is None else ModPermission(value)

    async def invites(self, community_id: int) -> list[Member]:
        statement = (
            select(
                moderator_invites.c.account_id,
                accounts.c.name,
                moderator_invites.c.created,
                moderator_invites.c.permissions,
            )
            .join(accounts, accounts.c.id == moderator_invites.c.account_id)
            .where(moderator_invites.c.community_id == community_id)
            .order_by(moderator_invites.c.created)
        )
        return [
            Member(
                account_id=row.account_id,
                name=row.name,
                created=row.created,
                permissions=ModPermission(row.permissions),
            )
            for row in await self.session.execute(statement)
        ]

    async def remove_moderator(self, community_id: int, account_id: int) -> bool:
        statement = (
            delete(moderators)
            .where(moderators.c.community_id == community_id, moderators.c.account_id == account_id)
            .returning(moderators.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def set_permissions(
        self, community_id: int, account_id: int, permissions: ModPermission
    ) -> bool:
        statement = (
            update(moderators)
            .where(moderators.c.community_id == community_id, moderators.c.account_id == account_id)
            .values(permissions=int(permissions))
            .returning(moderators.c.account_id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def log(
        self,
        community_id: int,
        mod_id: int,
        action: str,
        target: Fullname | None = None,
        details: str = '',
    ) -> None:
        await self.session.execute(
            modlog.insert().values(
                community_id=community_id,
                mod_id=mod_id,
                action=action,
                target_type=target.kind.value if target else None,
                target_id=target.id if target else None,
                details=details,
            )
        )

    async def actions(
        self,
        community_ids: list[int],
        *,
        limit: int,
        before_id: UUID | None = None,
        mod_name: str | None = None,
        action: str | None = None,
    ) -> list[ModAction]:
        statement = (
            select(
                modlog.c.id,
                modlog.c.community_id,
                communities.c.name.label('community_name'),
                accounts.c.name.label('mod_name'),
                modlog.c.action,
                modlog.c.target_type,
                modlog.c.target_id,
                modlog.c.details,
                modlog.c.created,
            )
            .join(accounts, accounts.c.id == modlog.c.mod_id)
            .join(communities, communities.c.id == modlog.c.community_id)
            .where(modlog.c.community_id.in_(community_ids or [-1]))
            .order_by(modlog.c.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            statement = statement.where(modlog.c.id < before_id)
        if mod_name:
            statement = statement.where(accounts.c.name == mod_name)
        if action:
            statement = statement.where(modlog.c.action == action)
        return [
            ModAction(
                id=row.id,
                community_id=row.community_id,
                community_name=row.community_name,
                mod_name=row.mod_name,
                action=row.action,
                target=Fullname(Kind(row.target_type), row.target_id) if row.target_type else None,
                details=row.details,
                created=row.created,
            )
            for row in await self.session.execute(statement)
        ]

    async def _recount_reports(self, thing: Fullname) -> None:
        table = _table(thing)
        count = (
            select(func.count())
            .where(reports.c.thing_type == thing.kind.value, reports.c.thing_id == thing.id)
            .scalar_subquery()
        )
        await self.session.execute(
            update(table).where(table.c.id == thing.id).values(reported=count)
        )

    async def report(self, thing: Fullname, reporter_id: int, reason: str) -> bool:
        statement = (
            upsert(reports)
            .values(
                thing_type=thing.kind.value,
                thing_id=thing.id,
                reporter_id=reporter_id,
                reason=reason,
            )
            .on_conflict_do_nothing()
            .returning(reports.c.id)
        )
        added = (await self.session.execute(statement)).first() is not None
        if added:
            await self._recount_reports(thing)
        return added

    async def report_reasons(self, things: list[Fullname]) -> dict[Fullname, list[str]]:
        if not things:
            return {}
        pairs = [(t.kind.value, t.id) for t in things]
        statement = (
            select(reports.c.thing_type, reports.c.thing_id, reports.c.reason)
            .where(tuple_(reports.c.thing_type, reports.c.thing_id).in_(pairs))
            .order_by(reports.c.id)
        )
        found: dict[Fullname, list[str]] = {}
        for row in await self.session.execute(statement):
            found.setdefault(Fullname(Kind(row.thing_type), row.thing_id), []).append(row.reason)
        return found

    async def clear_reports(self, thing: Fullname) -> None:
        await self.session.execute(
            delete(reports).where(
                reports.c.thing_type == thing.kind.value, reports.c.thing_id == thing.id
            )
        )
        await self._recount_reports(thing)

    async def set_removed(
        self, thing: Fullname, *, removed: bool, spam: bool, removed_by: str | None
    ) -> None:
        table = _table(thing)
        await self.session.execute(
            update(table)
            .where(table.c.id == thing.id)
            .values(
                removed=removed,
                spam=spam,
                removed_by=removed_by,
                approved_by=None,
                approved_at=None,
            )
        )

    async def remove_all_by(self, author_id: int) -> list[RemovedThing]:
        removing = {
            'removed': True,
            'spam': False,
            'removed_by': 'admin',
            'approved_by': None,
            'approved_at': None,
        }
        removed_links = await self.session.execute(
            update(links)
            .where(links.c.author_id == author_id, links.c.removed_by.is_distinct_from('admin'))
            .values(removing)
            .returning(links.c.id, links.c.community_id)
        )
        removed_comments = await self.session.execute(
            update(comments)
            .where(
                comments.c.author_id == author_id,
                comments.c.removed_by.is_distinct_from('admin'),
                links.c.id == comments.c.link_id,
            )
            .values(removing)
            .returning(comments.c.id, comments.c.link_id, links.c.community_id)
        )
        return [
            RemovedThing(Fullname(Kind.LINK, row.id), row.id, row.community_id)
            for row in removed_links
        ] + [
            RemovedThing(Fullname(Kind.COMMENT, row.id), row.link_id, row.community_id)
            for row in removed_comments
        ]

    async def set_approved(self, thing: Fullname, *, by: int, at: datetime) -> None:
        table = _table(thing)
        await self.session.execute(
            update(table)
            .where(table.c.id == thing.id)
            .values(removed=False, spam=False, removed_by=None, approved_by=by, approved_at=at)
        )
        await self.clear_reports(thing)

    async def set_ignore_reports(self, thing: Fullname, *, ignore: bool) -> None:
        table = _table(thing)
        await self.session.execute(
            update(table).where(table.c.id == thing.id).values(ignore_reports=ignore)
        )

    async def set_distinguished(self, thing: Fullname, how: str | None) -> None:
        table = _table(thing)
        await self.session.execute(
            update(table).where(table.c.id == thing.id).values(distinguished=how)
        )

    async def set_link_flags(
        self,
        link_id: int,
        *,
        locked: bool | None = None,
        stickied: bool | None = None,
        nsfw: bool | None = None,
    ) -> None:
        values = {
            name: value
            for name, value in (('locked', locked), ('stickied', stickied), ('nsfw', nsfw))
            if value is not None
        }
        if values:
            await self.session.execute(update(links).where(links.c.id == link_id).values(**values))

    async def set_sticky_comment(self, link_id: int, comment_id: int | None) -> None:
        await self.session.execute(
            update(links).where(links.c.id == link_id).values(sticky_comment_id=comment_id)
        )

    async def queue(
        self, community_ids: list[int] | None, queue: Queue, *, limit: int, after: QueueEntry | None
    ) -> list[QueueEntry]:
        parts = []
        for kind, table in TABLES.items():
            if queue is Queue.UNMODERATED and kind is Kind.COMMENT:
                continue
            # Comments find their community through their link.
            source = (
                table if kind is Kind.LINK else table.join(links, links.c.id == table.c.link_id)
            )
            reported = and_(table.c.reported > 0, table.c.ignore_reports == false())
            condition = {
                Queue.REPORTS: and_(reported, table.c.removed == false()),
                Queue.SPAM: or_(table.c.removed == true(), table.c.spam == true()),
                Queue.MODQUEUE: or_(
                    and_(reported, table.c.removed == false()),
                    and_(table.c.removed_by == 'filter', table.c.approved_by.is_(None)),
                ),
                Queue.UNMODERATED: and_(
                    table.c.approved_by.is_(None),
                    table.c.removed == false(),
                    table.c.spam == false(),
                ),
            }[queue]
            parts.append(
                select(
                    literal(kind.value).label('kind'),
                    literal(1 if kind is Kind.LINK else 0, Integer).label('rank'),
                    table.c.id.label('thing_id'),
                    table.c.created.label('created'),
                )
                .select_from(source)
                .where(
                    true()
                    if community_ids is None
                    else links.c.community_id.in_(community_ids or [-1]),
                    table.c.deleted == false(),
                    condition,
                )
            )
        entries = union_all(*parts).subquery('entries')
        statement = select(entries).order_by(
            entries.c.created.desc(), entries.c.rank.desc(), entries.c.thing_id.desc()
        )
        if after is not None:
            rank = 1 if after.thing.kind is Kind.LINK else 0
            statement = statement.where(
                tuple_(entries.c.created, entries.c.rank, entries.c.thing_id)
                < (after.created, rank, after.thing.id)
            )
        rows = await self.session.execute(statement.limit(limit))
        return [
            QueueEntry(thing=Fullname(Kind(row.kind), row.thing_id), created=row.created)
            for row in rows
        ]
