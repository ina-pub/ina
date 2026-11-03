"""add comments

Revision ID: bea6b752cce3
Revises: 2e4161db015e
Create Date: 2026-11-01 23:34:56.268191
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


class Ltree(sa.types.UserDefinedType[str]):
    """Postgres ltree, defined here so this revision never depends on app code."""

    cache_ok = True

    def get_col_spec(self, **_: object) -> str:
        return 'LTREE'


revision: str = 'bea6b752cce3'
down_revision: str | Sequence[str] | None = '2e4161db015e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('create extension if not exists ltree')
    op.create_table(
        'comments',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('link_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('path', Ltree(), nullable=False),
        sa.Column('depth', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.BigInteger(), nullable=False),
        sa.Column('body_md', sa.Text(), nullable=False),
        sa.Column('body_html', sa.Text(), nullable=False),
        sa.Column('score', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('upvotes', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('downvotes', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('confidence', sa.Double(), server_default='0', nullable=False),
        sa.Column('controversy_score', sa.Double(), server_default='0', nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column('edited', sa.DateTime(timezone=True), nullable=True),
        sa.Column('removed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('removed_by', sa.Text(), nullable=True),
        sa.Column('spam', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('distinguished', sa.Text(), nullable=True),
        sa.Column('sendreplies', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('ignore_reports', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('reported', sa.Integer(), server_default='0', nullable=False),
        sa.Column('new', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('ip', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['author_id'], ['accounts.id'], name=op.f('fk_comments_author_id_accounts')
        ),
        sa.ForeignKeyConstraint(['link_id'], ['links.id'], name=op.f('fk_comments_link_id_links')),
        sa.ForeignKeyConstraint(
            ['parent_id'], ['comments.id'], name=op.f('fk_comments_parent_id_comments')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_comments')),
    )
    op.create_index(
        'ix_comments_author', 'comments', ['author_id', sa.literal_column('id DESC')], unique=False
    )
    op.create_index('ix_comments_link', 'comments', ['link_id'], unique=False)
    op.create_index('ix_comments_path', 'comments', ['path'], unique=False, postgresql_using='gist')
    op.add_column('links', sa.Column('sticky_comment_id', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('links', 'sticky_comment_id')
    op.drop_index('ix_comments_path', table_name='comments', postgresql_using='gist')
    op.drop_index('ix_comments_link', table_name='comments')
    op.drop_index('ix_comments_author', table_name='comments')
    op.drop_table('comments')
