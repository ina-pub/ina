"""argon2id hashing. Hashing is slow on purpose, so it runs off the event loop."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

if TYPE_CHECKING:
    from app.settings import Settings


class Passwords:
    def __init__(self, settings: Settings) -> None:
        self.hasher = PasswordHasher(
            time_cost=settings.password_time_cost,
            memory_cost=settings.password_memory_kib,
            parallelism=settings.password_parallelism,
        )

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self.hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        try:
            return await asyncio.to_thread(self.hasher.verify, password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self.hasher.check_needs_rehash(password_hash)
