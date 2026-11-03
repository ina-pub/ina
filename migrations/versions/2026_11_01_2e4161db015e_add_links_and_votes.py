"""add links and votes

Revision ID: 2e4161db015e
Revises: 1e028602d175
Create Date: 2026-11-01 22:48:55.245800
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = '2e4161db015e'
down_revision: str | Sequence[str] | None = '1e028602d175'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'votes',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('thing_type', sa.Text(), nullable=False),
        sa.Column('thing_id', sa.BigInteger(), nullable=False),
        sa.Column('direction', sa.SmallInteger(), nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.CheckConstraint("thing_type in ('t1', 't3')", name=op.f('ck_votes_thing_type')),
        sa.CheckConstraint('direction in (-1, 0, 1)', name=op.f('ck_votes_direction')),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_votes_account_id_accounts')
        ),
        sa.PrimaryKeyConstraint('account_id', 'thing_type', 'thing_id', name=op.f('pk_votes')),
    )
    op.create_index(
        op.f('ix_votes_thing_type_thing_id_direction'),
        'votes',
        ['thing_type', 'thing_id', 'direction'],
        unique=False,
    )
    op.create_table(
        'links',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('community_id', sa.BigInteger(), nullable=False),
        sa.Column('author_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('domain', sa.Text(), nullable=False),
        sa.Column('is_self', sa.Boolean(), nullable=False),
        sa.Column('selftext_md', sa.Text(), server_default='', nullable=False),
        sa.Column('selftext_html', sa.Text(), server_default='', nullable=False),
        sa.Column('nsfw', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('spoiler', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('flair_text', sa.Text(), nullable=True),
        sa.Column('flair_css', sa.Text(), nullable=True),
        sa.Column('score', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('upvotes', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('downvotes', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('hot_score', sa.Double(), server_default='0', nullable=False),
        sa.Column('controversy_score', sa.Double(), server_default='0', nullable=False),
        sa.Column('comment_count', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column('edited', sa.DateTime(timezone=True), nullable=True),
        sa.Column('removed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('removed_by', sa.Text(), nullable=True),
        sa.Column('spam', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('locked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('stickied', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('suggested_sort', sa.Text(), nullable=True),
        sa.Column('contest_mode', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('sendreplies', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('ignore_reports', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column(
            'disable_comments', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column('media', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('ip', sa.Text(), nullable=True),
        sa.Column('reported', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(
            ['author_id'], ['accounts.id'], name=op.f('fk_links_author_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'], ['communities.id'], name=op.f('fk_links_community_id_communities')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_links')),
    )
    op.create_index(
        'ix_links_author', 'links', ['author_id', sa.literal_column('id DESC')], unique=False
    )
    op.create_index('ix_links_created', 'links', ['created'], unique=False)
    op.create_index(
        'ix_links_hot',
        'links',
        [sa.literal_column('hot_score DESC'), sa.literal_column('id DESC')],
        unique=False,
    )
    op.create_index(
        'ix_links_community_created_score',
        'links',
        ['community_id', 'created', sa.literal_column('score DESC')],
        unique=False,
    )
    op.create_index(
        'ix_links_community_hot',
        'links',
        ['community_id', sa.literal_column('hot_score DESC'), sa.literal_column('id DESC')],
        unique=False,
    )
    op.create_index(
        'ix_links_community_new',
        'links',
        ['community_id', sa.literal_column('id DESC')],
        unique=False,
    )
    op.create_index('ix_links_community_url', 'links', ['community_id', 'url'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_links_community_url', table_name='links')
    op.drop_index('ix_links_community_new', table_name='links')
    op.drop_index('ix_links_community_hot', table_name='links')
    op.drop_index('ix_links_community_created_score', table_name='links')
    op.drop_index('ix_links_hot', table_name='links')
    op.drop_index('ix_links_created', table_name='links')
    op.drop_index('ix_links_author', table_name='links')
    op.drop_table('links')
    op.drop_index(op.f('ix_votes_thing_type_thing_id_direction'), table_name='votes')
    op.drop_table('votes')
