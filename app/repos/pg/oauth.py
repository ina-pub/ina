from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, insert, select, update

from app.repos.base import AppKind, Authorization, OAuthClient, OAuthToken
from app.repos.pg.tables import oauth_clients, oauth_tokens

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Row
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.repos.base import AppFields

TOKEN_COLUMNS = (
    oauth_tokens.c.id,
    oauth_tokens.c.client_id,
    oauth_tokens.c.account_id,
    oauth_tokens.c.scopes,
    oauth_tokens.c.access_expires,
    oauth_tokens.c.refresh_hash,
    oauth_tokens.c.created,
)


def _fields(fields: AppFields) -> dict[str, str]:
    return {
        'name': fields.name,
        'description': fields.description,
        'about_url': fields.about_url,
        'redirect_uri': fields.redirect_uri,
    }


def _client(row: Row[*tuple[Any, ...]]) -> OAuthClient:
    values = row._asdict()
    return OAuthClient(**{**values, 'kind': AppKind(values['kind'])})


def _token(row: Row[*tuple[Any, ...]]) -> OAuthToken:
    return OAuthToken(
        id=row.id,
        client_id=row.client_id,
        account_id=row.account_id,
        scopes=frozenset(row.scopes.split()),
        access_expires=row.access_expires,
        refreshable=row.refresh_hash is not None,
        created=row.created,
    )


class PgOAuthRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_client(
        self,
        client_id: str,
        owner_id: int,
        kind: AppKind,
        fields: AppFields,
        secret_hash: str | None,
    ) -> OAuthClient:
        statement = (
            insert(oauth_clients)
            .values(
                id=client_id,
                owner_id=owner_id,
                kind=kind.value,
                secret_hash=secret_hash,
                **_fields(fields),
            )
            .returning(*oauth_clients.c)
        )
        return _client((await self.session.execute(statement)).one())

    async def client(self, client_id: str) -> OAuthClient | None:
        statement = select(*oauth_clients.c).where(oauth_clients.c.id == client_id)
        row = (await self.session.execute(statement)).one_or_none()
        return _client(row) if row else None

    async def owned(self, owner_id: int) -> list[OAuthClient]:
        statement = (
            select(*oauth_clients.c)
            .where(oauth_clients.c.owner_id == owner_id)
            .order_by(oauth_clients.c.created)
        )
        return [_client(row) for row in await self.session.execute(statement)]

    async def update_client(self, client_id: str, fields: AppFields) -> None:
        await self.session.execute(
            update(oauth_clients).where(oauth_clients.c.id == client_id).values(**_fields(fields))
        )

    async def set_secret(self, client_id: str, secret_hash: str) -> None:
        await self.session.execute(
            update(oauth_clients)
            .where(oauth_clients.c.id == client_id)
            .values(secret_hash=secret_hash)
        )
        # A new secret ends what the old one let the app do for itself.
        await self.session.execute(
            delete(oauth_tokens).where(
                oauth_tokens.c.client_id == client_id, oauth_tokens.c.account_id.is_(None)
            )
        )

    async def delete_client(self, client_id: str) -> None:
        await self.session.execute(delete(oauth_clients).where(oauth_clients.c.id == client_id))

    async def add_token(
        self,
        client_id: str,
        account_id: int | None,
        scopes: frozenset[str],
        *,
        access_hash: str,
        access_expires: datetime,
        refresh_hash: str | None,
    ) -> OAuthToken:
        statement = (
            insert(oauth_tokens)
            .values(
                client_id=client_id,
                account_id=account_id,
                scopes=' '.join(sorted(scopes)),
                access_hash=access_hash,
                access_expires=access_expires,
                refresh_hash=refresh_hash,
            )
            .returning(*TOKEN_COLUMNS)
        )
        return _token((await self.session.execute(statement)).one())

    async def by_access(self, access_hash: str) -> OAuthToken | None:
        statement = select(*TOKEN_COLUMNS).where(oauth_tokens.c.access_hash == access_hash)
        row = (await self.session.execute(statement)).one_or_none()
        return _token(row) if row else None

    async def by_refresh(self, refresh_hash: str) -> OAuthToken | None:
        statement = select(*TOKEN_COLUMNS).where(oauth_tokens.c.refresh_hash == refresh_hash)
        row = (await self.session.execute(statement)).one_or_none()
        return _token(row) if row else None

    async def rotate(
        self, token_id: int, *, access_hash: str, access_expires: datetime, refresh_hash: str
    ) -> None:
        await self.session.execute(
            update(oauth_tokens)
            .where(oauth_tokens.c.id == token_id)
            .values(
                access_hash=access_hash, access_expires=access_expires, refresh_hash=refresh_hash
            )
        )

    async def delete_token(self, token_id: int) -> None:
        await self.session.execute(delete(oauth_tokens).where(oauth_tokens.c.id == token_id))

    async def authorizations(self, account_id: int) -> list[Authorization]:
        grants = (
            select(
                oauth_tokens.c.client_id,
                func.string_agg(oauth_tokens.c.scopes, ' ').label('scopes'),
                func.min(oauth_tokens.c.created).label('since'),
            )
            .where(oauth_tokens.c.account_id == account_id)
            .group_by(oauth_tokens.c.client_id)
            .subquery()
        )
        statement = (
            select(*oauth_clients.c, grants.c.scopes, grants.c.since)
            .join(grants, grants.c.client_id == oauth_clients.c.id)
            .order_by(grants.c.since)
        )
        found = []
        for row in await self.session.execute(statement):
            values = row._asdict()
            scopes, since = values.pop('scopes'), values.pop('since')
            client = OAuthClient(**{**values, 'kind': AppKind(values['kind'])})
            found.append(
                Authorization(client=client, scopes=frozenset(scopes.split()), since=since)
            )
        return found

    async def revoke_client(self, account_id: int, client_id: str) -> None:
        await self.session.execute(
            delete(oauth_tokens).where(
                oauth_tokens.c.account_id == account_id, oauth_tokens.c.client_id == client_id
            )
        )
