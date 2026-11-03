"""add moderation tables

Revision ID: 38320961c168
Revises: c639d2158d6f
Create Date: 2026-11-02 01:01:26.311878
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = '38320961c168'
down_revision: str | Sequence[str] | None = 'c639d2158d6f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'reports',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('thing_type', sa.Text(), nullable=False),
        sa.Column('thing_id', sa.BigInteger(), nullable=False),
        sa.Column('reporter_id', sa.BigInteger(), nullable=False),
        sa.Column('reason', sa.Text(), server_default='', nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.CheckConstraint("thing_type in ('t1', 't3')", name=op.f('ck_reports_thing_type')),
        sa.ForeignKeyConstraint(
            ['reporter_id'], ['accounts.id'], name=op.f('fk_reports_reporter_id_accounts')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_reports')),
        sa.UniqueConstraint(
            'thing_type',
            'thing_id',
            'reporter_id',
            name=op.f('uq_reports_thing_type_thing_id_reporter_id'),
        ),
    )
    op.create_table(
        'bans',
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('note', sa.Text(), server_default='', nullable=False),
        sa.Column('message', sa.Text(), server_default='', nullable=False),
        sa.Column('expires', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_bans_account_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['accounts.id'], name=op.f('fk_bans_created_by_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'], ['communities.id'], name=op.f('fk_bans_community_id_communities')
        ),
        sa.PrimaryKeyConstraint('community_id', 'account_id', name=op.f('pk_bans')),
    )
    op.create_table(
        'contributors',
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('added_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_contributors_account_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['added_by'], ['accounts.id'], name=op.f('fk_contributors_added_by_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'],
            ['communities.id'],
            name=op.f('fk_contributors_community_id_communities'),
        ),
        sa.PrimaryKeyConstraint('community_id', 'account_id', name=op.f('pk_contributors')),
    )
    op.create_table(
        'moderator_invites',
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('permissions', sa.Integer(), nullable=False),
        sa.Column('invited_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_moderator_invites_account_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['invited_by'], ['accounts.id'], name=op.f('fk_moderator_invites_invited_by_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'],
            ['communities.id'],
            name=op.f('fk_moderator_invites_community_id_communities'),
        ),
        sa.PrimaryKeyConstraint('community_id', 'account_id', name=op.f('pk_moderator_invites')),
    )
    op.create_table(
        'modlog',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column('mod_id', sa.BigInteger(), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('target_type', sa.Text(), nullable=True),
        sa.Column('target_id', sa.BigInteger(), nullable=True),
        sa.Column('details', sa.Text(), server_default='', nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['mod_id'], ['accounts.id'], name=op.f('fk_modlog_mod_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'], ['communities.id'], name=op.f('fk_modlog_community_id_communities')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_modlog')),
    )
    op.create_index(
        'ix_modlog_community',
        'modlog',
        ['community_id', sa.literal_column('id DESC')],
        unique=False,
    )
    op.add_column('comments', sa.Column('approved_by', sa.BigInteger(), nullable=True))
    op.add_column('comments', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f('fk_comments_approved_by_accounts'), 'comments', 'accounts', ['approved_by'], ['id']
    )
    op.add_column('links', sa.Column('distinguished', sa.Text(), nullable=True))
    op.add_column('links', sa.Column('approved_by', sa.BigInteger(), nullable=True))
    op.add_column('links', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        op.f('fk_links_approved_by_accounts'), 'links', 'accounts', ['approved_by'], ['id']
    )
    op.add_column(
        'communities', sa.Column('automoderator', sa.Text(), server_default='', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('communities', 'automoderator')
    op.drop_constraint(op.f('fk_links_approved_by_accounts'), 'links', type_='foreignkey')
    op.drop_column('links', 'approved_at')
    op.drop_column('links', 'approved_by')
    op.drop_column('links', 'distinguished')
    op.drop_constraint(op.f('fk_comments_approved_by_accounts'), 'comments', type_='foreignkey')
    op.drop_column('comments', 'approved_at')
    op.drop_column('comments', 'approved_by')
    op.drop_index('ix_modlog_community', table_name='modlog')
    op.drop_table('modlog')
    op.drop_table('moderator_invites')
    op.drop_table('contributors')
    op.drop_table('bans')
    op.drop_table('reports')
