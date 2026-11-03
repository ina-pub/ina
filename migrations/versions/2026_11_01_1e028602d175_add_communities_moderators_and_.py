"""add communities, moderators and subscriptions

Revision ID: 1e028602d175
Revises: 89ea7d8e96d2
Create Date: 2026-11-01 22:32:18.324832
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = '1e028602d175'
down_revision: str | Sequence[str] | None = '89ea7d8e96d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'communities',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('name', postgresql.CITEXT(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('description_md', sa.Text(), server_default='', nullable=False),
        sa.Column('description_html', sa.Text(), server_default='', nullable=False),
        sa.Column('sidebar_md', sa.Text(), server_default='', nullable=False),
        sa.Column('sidebar_html', sa.Text(), server_default='', nullable=False),
        sa.Column('type', sa.Text(), server_default='public', nullable=False),
        sa.Column('nsfw', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('stylesheet_css', sa.Text(), server_default='', nullable=False),
        sa.Column(
            'settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False
        ),
        sa.Column('subscriber_count', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column('creator_id', sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "type in ('public', 'restricted', 'private')", name=op.f('ck_communities_type')
        ),
        sa.ForeignKeyConstraint(
            ['creator_id'], ['accounts.id'], name=op.f('fk_communities_creator_id_accounts')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_communities')),
        sa.UniqueConstraint('name', name=op.f('uq_communities_name')),
    )
    op.create_index(
        op.f('ix_communities_subscriber_count_id'),
        'communities',
        ['subscriber_count', 'id'],
        unique=False,
    )
    op.create_table(
        'moderators',
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('permissions', sa.Integer(), nullable=False),
        sa.Column('added_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_moderators_account_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['added_by'], ['accounts.id'], name=op.f('fk_moderators_added_by_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'],
            ['communities.id'],
            name=op.f('fk_moderators_community_id_communities'),
        ),
        sa.PrimaryKeyConstraint('community_id', 'account_id', name=op.f('pk_moderators')),
    )
    op.create_index(op.f('ix_moderators_account_id'), 'moderators', ['account_id'], unique=False)
    op.create_table(
        'subscriptions',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_subscriptions_account_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'],
            ['communities.id'],
            name=op.f('fk_subscriptions_community_id_communities'),
        ),
        sa.PrimaryKeyConstraint('account_id', 'community_id', name=op.f('pk_subscriptions')),
    )
    op.create_index(
        op.f('ix_subscriptions_community_id'), 'subscriptions', ['community_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_subscriptions_community_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_moderators_account_id'), table_name='moderators')
    op.drop_table('moderators')
    op.drop_index(op.f('ix_communities_subscriber_count_id'), table_name='communities')
    op.drop_table('communities')
