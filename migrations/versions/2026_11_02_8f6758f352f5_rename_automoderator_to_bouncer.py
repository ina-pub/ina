"""rename automoderator to bouncer

Revision ID: 8f6758f352f5
Revises: b7afcc599d3c
Create Date: 2026-11-02 17:42:14.925673
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = '8f6758f352f5'
down_revision: str | Sequence[str] | None = 'b7afcc599d3c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The system account, which nobody can sign in to (see app.services.bouncer.system_account).
SYSTEM_HASH = '!'


def _rename(old: str, new: str, old_page: str, new_page: str) -> None:
    taken = op.get_bind().execute(
        sa.text('select 1 from accounts where name = :new and password_hash <> :hash'),
        {'new': new, 'hash': SYSTEM_HASH},
    )
    if taken.first() is not None:
        msg = f'an account named {new} already exists; rename it before migrating'
        raise RuntimeError(msg)
    op.execute(
        sa.text(
            'update accounts set name = :new where name = :old and password_hash = :hash'
        ).bindparams(new=new, old=old, hash=SYSTEM_HASH)
    )
    op.execute(
        sa.text('update wiki_pages set name = :new where name = :old').bindparams(
            new=new_page, old=old_page
        )
    )


def upgrade() -> None:
    op.alter_column('communities', 'automoderator', new_column_name='bouncer_rules')
    _rename('AutoModerator', 'Bouncer', 'config/automoderator', 'config/bouncer')


def downgrade() -> None:
    _rename('Bouncer', 'AutoModerator', 'config/bouncer', 'config/automoderator')
    op.alter_column('communities', 'bouncer_rules', new_column_name='automoderator')
