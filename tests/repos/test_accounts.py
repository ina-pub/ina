"""The AccountRepo contract. Every implementation runs the same tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infra import db
from app.repos.base import AccountRepo, NameTakenError, Prefs
from app.repos.pg.accounts import PgAccountRepo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.settings import Settings


@pytest.fixture(params=['pg'])
async def repo(settings: Settings) -> AsyncIterator[AccountRepo]:
    engine = db.create_engine(settings)
    async with async_sessionmaker(engine)() as session, session.begin():
        yield PgAccountRepo(session)
    await engine.dispose()


async def test_create_and_find(repo: AccountRepo) -> None:
    created = await repo.create(name='Alice', password_hash='h', email='a@example.com')
    assert created.id > 0
    assert created.prefs == Prefs()
    assert created.session_version == 0
    assert await repo.by_id(created.id) == created
    assert await repo.by_name('aLiCe') == created
    assert await repo.by_name('bob') is None
    assert await repo.by_id(created.id + 1000) is None


async def test_names_are_unique_ignoring_case(repo: AccountRepo) -> None:
    await repo.create(name='alice', password_hash='h', email=None)
    with pytest.raises(NameTakenError):
        await repo.create(name='ALICE', password_hash='h', email=None)
    # The failed insert must not have poisoned the transaction.
    assert await repo.by_name('alice') is not None


async def test_set_password_signs_out_sessions(repo: AccountRepo) -> None:
    account = await repo.create(name='alice', password_hash='old', email=None)
    assert await repo.set_password(account.id, 'new') == 1
    await repo.rehash_password(account.id, 'stronger')
    found = await repo.by_id(account.id)
    assert found is not None
    assert (found.password_hash, found.session_version) == ('stronger', 1)
    assert await repo.bump_session_version(account.id) == 2


async def test_prefs_and_email(repo: AccountRepo) -> None:
    account = await repo.create(name='alice', password_hash='h', email='a@example.com')
    await repo.set_prefs(account.id, Prefs(numsites=100, min_link_score=None))
    await repo.set_email(account.id, None)
    found = await repo.by_id(account.id)
    assert found is not None
    assert found.prefs.numsites == 100
    assert found.prefs.min_link_score is None
    assert found.email is None
    assert found.email_verified is False
