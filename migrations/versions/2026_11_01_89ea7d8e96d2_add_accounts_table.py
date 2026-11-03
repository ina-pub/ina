"""add accounts table

Revision ID: 89ea7d8e96d2
Revises:
Create Date: 2026-11-01 22:02:41.815062
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = '89ea7d8e96d2'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('create extension if not exists citext')
    op.create_table(
        'accounts',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('name', postgresql.CITEXT(), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('email', postgresql.CITEXT(), nullable=True),
        sa.Column('email_verified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column('link_karma', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('comment_karma', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column(
            'prefs', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False
        ),
        sa.Column('deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('suspended', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('in_timeout', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('spammer', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('inbox_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('modmsgtime', sa.DateTime(timezone=True), nullable=True),
        sa.Column('otp_secret', sa.Text(), nullable=True),
        sa.Column('session_version', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts')),
        sa.UniqueConstraint('name', name=op.f('uq_accounts_name')),
    )


def downgrade() -> None:
    op.drop_table('accounts')
