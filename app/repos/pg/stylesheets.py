from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as upsert

from app.repos.base import CommunityImage, TooManyImagesError
from app.repos.pg.tables import communities, community_images, community_stylesheets

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

IMAGE_COLUMNS = (
    community_images.c.name,
    community_images.c.key,
    community_images.c.width,
    community_images.c.height,
    community_images.c.bytes,
)


class PgStylesheetRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def images(self, community_id: int) -> list[CommunityImage]:
        statement = (
            select(*IMAGE_COLUMNS)
            .where(community_images.c.community_id == community_id)
            .order_by(community_images.c.name)
        )
        return [CommunityImage(**row._asdict()) for row in await self.session.execute(statement)]

    async def add_image(self, community_id: int, image: CommunityImage, *, limit: int) -> None:
        others = (
            await self.session.execute(
                select(func.count()).where(
                    community_images.c.community_id == community_id,
                    community_images.c.name != image.name,
                )
            )
        ).scalar_one()
        if others >= limit:
            raise TooManyImagesError
        values = {
            'key': image.key,
            'width': image.width,
            'height': image.height,
            'bytes': image.bytes,
            'created': datetime.now(UTC),
        }
        statement = upsert(community_images).values(
            community_id=community_id, name=image.name, **values
        )
        statement = statement.on_conflict_do_update(
            index_elements=[community_images.c.community_id, community_images.c.name], set_=values
        )
        await self.session.execute(statement)

    async def delete_image(self, community_id: int, name: str) -> bool:
        statement = (
            delete(community_images)
            .where(community_images.c.community_id == community_id, community_images.c.name == name)
            .returning(community_images.c.name)
        )
        return (await self.session.execute(statement)).first() is not None

    async def stylesheet(self, community_id: int) -> str | None:
        statement = select(community_stylesheets.c.css).where(
            community_stylesheets.c.community_id == community_id
        )
        return (await self.session.execute(statement)).scalar()

    async def set_stylesheet(self, community_id: int, css: str, version: str) -> None:
        now = datetime.now(UTC)
        statement = upsert(community_stylesheets).values(
            community_id=community_id, css=css, updated=now
        )
        statement = statement.on_conflict_do_update(
            index_elements=[community_stylesheets.c.community_id], set_={'css': css, 'updated': now}
        )
        await self.session.execute(statement)
        await self.session.execute(
            update(communities)
            .where(communities.c.id == community_id)
            .values(stylesheet_hash=version)
        )
