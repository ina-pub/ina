from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import msgspec
from sqlalchemy import and_, exists, false, func, insert, not_, or_, select, true, tuple_, update
from sqlalchemy.exc import IntegrityError

from app.repos.base import Community, CommunitySettings, Moderator, ModPermission, NameTakenError
from app.repos.pg.tables import accounts, bans, communities, contributors, moderators, mutes

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement, Row, Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repos.base import CommunityText

COLUMNS = (
    communities.c.id,
    communities.c.name,
    communities.c.title,
    communities.c.description_md,
    communities.c.description_html,
    communities.c.sidebar_md,
    communities.c.sidebar_html,
    communities.c.type,
    communities.c.nsfw,
    communities.c.settings,
    communities.c.subscriber_count,
    communities.c.created,
    communities.c.bouncer_rules,
    communities.c.stylesheet_hash,
    communities.c.banned,
)


def to_community(row: Row[*tuple[Any, ...]]) -> Community:
    return Community(
        id=row.id,
        name=row.name,
        title=row.title,
        description_md=row.description_md,
        description_html=row.description_html,
        sidebar_md=row.sidebar_md,
        sidebar_html=row.sidebar_html,
        type=row.type,
        nsfw=row.nsfw,
        settings=msgspec.convert(row.settings, CommunitySettings),
        subscriber_count=row.subscriber_count,
        created=row.created,
        bouncer_rules=row.bouncer_rules,
        stylesheet_hash=row.stylesheet_hash,
        banned=row.banned,
    )


def text_values(text: CommunityText) -> dict[str, object]:
    return {
        'title': text.title,
        'description_md': text.description_md,
        'description_html': text.description_html,
        'sidebar_md': text.sidebar_md,
        'sidebar_html': text.sidebar_html,
        'type': text.type,
        'nsfw': text.nsfw,
        'settings': msgspec.to_builtins(text.settings),
    }


class PgCommunityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _all(self, statement: Select[Any]) -> list[Community]:
        return [to_community(row) for row in await self.session.execute(statement)]

    async def by_id(self, community_id: int) -> Community | None:
        statement = select(*COLUMNS).where(communities.c.id == community_id)
        row = (await self.session.execute(statement)).first()
        return to_community(row) if row else None

    async def by_ids(self, community_ids: list[int]) -> dict[int, Community]:
        if not community_ids:
            return {}
        found = await self._all(select(*COLUMNS).where(communities.c.id.in_(community_ids)))
        return {community.id: community for community in found}

    async def max_id(self) -> int:
        value = (await self.session.execute(select(func.max(communities.c.id)))).scalar()
        return int(value or 0)

    async def by_name(self, name: str) -> Community | None:
        statement = select(*COLUMNS).where(communities.c.name == name)
        row = (await self.session.execute(statement)).first()
        return to_community(row) if row else None

    async def create(self, *, name: str, text: CommunityText, creator_id: int) -> Community:
        statement = (
            insert(communities)
            .values(name=name, creator_id=creator_id, **text_values(text))
            .returning(*COLUMNS)
        )
        try:
            async with self.session.begin_nested():
                row = (await self.session.execute(statement)).one()
        except IntegrityError as error:
            raise NameTakenError(name) from error
        return to_community(row)

    async def update(self, community_id: int, text: CommunityText) -> Community:
        statement = (
            update(communities)
            .where(communities.c.id == community_id)
            .values(**text_values(text))
            .returning(*COLUMNS)
        )
        return to_community((await self.session.execute(statement)).one())

    async def set_settings(self, community_id: int, settings: CommunitySettings) -> None:
        await self.session.execute(
            update(communities)
            .where(communities.c.id == community_id)
            .values(settings=msgspec.to_builtins(settings))
        )

    async def set_flags(
        self, community_id: int, *, banned: bool | None = None, nsfw: bool | None = None
    ) -> None:
        values = {
            name: value for name, value in (('banned', banned), ('nsfw', nsfw)) if value is not None
        }
        await self.session.execute(
            update(communities).where(communities.c.id == community_id).values(**values)
        )

    async def set_bouncer_rules(self, community_id: int, rules: str) -> None:
        await self.session.execute(
            update(communities).where(communities.c.id == community_id).values(bouncer_rules=rules)
        )

    async def popular(self, *, limit: int, after: Community | None) -> list[Community]:
        statement = (
            select(*COLUMNS)
            .where(communities.c.banned == false())
            .order_by(communities.c.subscriber_count.desc(), communities.c.id.desc())
        )
        if after is not None:
            position = tuple_(communities.c.subscriber_count, communities.c.id)
            statement = statement.where(position < (after.subscriber_count, after.id))
        return await self._all(statement.limit(limit))

    async def newest(
        self, *, limit: int, after: Community | None, include_banned: bool = False
    ) -> list[Community]:
        statement = select(*COLUMNS).order_by(communities.c.id.desc())
        if not include_banned:
            statement = statement.where(communities.c.banned == false())
        if after is not None:
            statement = statement.where(communities.c.id < after.id)
        return await self._all(statement.limit(limit))

    async def moderators(self, community_id: int) -> list[Moderator]:
        statement = (
            select(
                moderators.c.account_id,
                accounts.c.name,
                moderators.c.permissions,
                moderators.c.created,
            )
            .join(accounts, accounts.c.id == moderators.c.account_id)
            .where(moderators.c.community_id == community_id)
            .order_by(moderators.c.created, moderators.c.account_id)
        )
        return [
            Moderator(
                account_id=row.account_id,
                name=row.name,
                permissions=ModPermission(row.permissions),
                added=row.created,
            )
            for row in await self.session.execute(statement)
        ]

    async def permissions(self, community_id: int, account_id: int) -> ModPermission | None:
        statement = select(moderators.c.permissions).where(
            moderators.c.community_id == community_id, moderators.c.account_id == account_id
        )
        value = (await self.session.execute(statement)).scalar_one_or_none()
        return None if value is None else ModPermission(value)

    async def add_moderator(
        self,
        community_id: int,
        account_id: int,
        *,
        permissions: ModPermission,
        added_by: int | None,
    ) -> None:
        await self.session.execute(
            insert(moderators).values(
                community_id=community_id,
                account_id=account_id,
                permissions=int(permissions),
                added_by=added_by,
            )
        )

    async def moderated_by(
        self, account_id: int, *, limit: int, after: Community | None
    ) -> list[Community]:
        statement = (
            select(*COLUMNS)
            .join(moderators, moderators.c.community_id == communities.c.id)
            .where(moderators.c.account_id == account_id)
            .order_by(func.lower(communities.c.name))
        )
        if after is not None:
            statement = statement.where(func.lower(communities.c.name) > after.name.lower())
        return await self._all(statement.limit(limit))

    async def ids_by_names(self, names: list[str]) -> list[int]:
        if not names:
            return []
        statement = select(communities.c.id, communities.c.name).where(
            communities.c.name.in_(names)
        )
        found = {row.name.lower(): row.id for row in await self.session.execute(statement)}
        return [found[name.lower()] for name in names if name.lower() in found]

    async def is_muted(self, community_id: int, account_id: int) -> bool:
        statement = select(mutes.c.account_id).where(
            mutes.c.community_id == community_id,
            mutes.c.account_id == account_id,
            mutes.c.expires > datetime.now(UTC),
        )
        return (await self.session.execute(statement)).first() is not None

    async def standing(
        self, community_id: int, account_id: int
    ) -> tuple[ModPermission | None, bool, bool]:
        now = datetime.now(UTC)
        permissions = (
            select(moderators.c.permissions)
            .where(moderators.c.community_id == community_id, moderators.c.account_id == account_id)
            .scalar_subquery()
        )
        contributor = (
            select(func.count())
            .where(
                contributors.c.community_id == community_id, contributors.c.account_id == account_id
            )
            .scalar_subquery()
        )
        banned = (
            select(func.count())
            .where(
                bans.c.community_id == community_id,
                bans.c.account_id == account_id,
                or_(bans.c.expires.is_(None), bans.c.expires > now),
            )
            .scalar_subquery()
        )
        row = (await self.session.execute(select(permissions, contributor, banned))).one()
        mod = None if row[0] is None else ModPermission(row[0])
        return mod, bool(row[1]), bool(row[2])

    async def moderated_ids(self, account_id: int, *, needing: ModPermission) -> list[int]:
        statement = select(moderators.c.community_id).where(
            moderators.c.account_id == account_id,
            moderators.c.permissions.op('&')(int(needing)) == int(needing),
        )
        return list((await self.session.execute(statement)).scalars())

    async def defaults(self, *, limit: int) -> list[int]:
        statement = (
            select(communities.c.id)
            .where(
                communities.c.type == 'public',
                communities.c.nsfw == false(),
                communities.c.banned == false(),
                communities.c.settings['allow_top'].as_boolean().is_not(false()),
                communities.c.settings['quarantine'].as_boolean().is_not(true()),
            )
            .order_by(communities.c.subscriber_count.desc(), communities.c.id)
            .limit(limit)
        )
        return list((await self.session.execute(statement)).scalars())

    async def excluded_from_search(
        self, account_id: int | None, *, include_nsfw: bool
    ) -> list[int]:
        member: ColumnElement[bool] = false()
        if account_id is not None:
            member = or_(
                exists().where(
                    moderators.c.community_id == communities.c.id,
                    moderators.c.account_id == account_id,
                ),
                exists().where(
                    contributors.c.community_id == communities.c.id,
                    contributors.c.account_id == account_id,
                ),
            )
        excluded = or_(and_(communities.c.type == 'private', not_(member)), communities.c.banned)
        if not include_nsfw:
            excluded = or_(excluded, communities.c.nsfw == true())
        statement = select(communities.c.id).where(excluded).order_by(communities.c.id)
        return list((await self.session.execute(statement)).scalars())
