"""add messages and mentions

Revision ID: c639d2158d6f
Revises: bea6b752cce3
Create Date: 2026-11-02 00:48:52.500897
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = 'c639d2158d6f'
down_revision: str | Sequence[str] | None = 'bea6b752cce3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'messages',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('from_id', sa.BigInteger(), nullable=False),
        sa.Column('to_id', sa.BigInteger(), nullable=True),
        sa.Column('community_id', sa.BigInteger(), nullable=True),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('first_message_id', sa.BigInteger(), nullable=True),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('body_md', sa.Text(), nullable=False),
        sa.Column('body_html', sa.Text(), nullable=False),
        sa.Column('read', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column('from_community', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('display_author', sa.Text(), nullable=True),
        sa.Column('display_to', sa.Text(), nullable=True),
        sa.Column('to_collapse', sa.Boolean(), nullable=True),
        sa.Column('author_collapse', sa.Boolean(), nullable=True),
        sa.Column(
            'del_on_recipient', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column('ip', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['first_message_id'],
            ['messages.id'],
            name=op.f('fk_messages_first_message_id_messages'),
        ),
        sa.ForeignKeyConstraint(
            ['from_id'], ['accounts.id'], name=op.f('fk_messages_from_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['parent_id'], ['messages.id'], name=op.f('fk_messages_parent_id_messages')
        ),
        sa.ForeignKeyConstraint(
            ['community_id'], ['communities.id'], name=op.f('fk_messages_community_id_communities')
        ),
        sa.ForeignKeyConstraint(
            ['to_id'], ['accounts.id'], name=op.f('fk_messages_to_id_accounts')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_messages')),
    )
    op.create_index('ix_messages_first', 'messages', ['first_message_id'], unique=False)
    op.create_index(
        'ix_messages_from', 'messages', ['from_id', sa.literal_column('id DESC')], unique=False
    )
    op.create_index(
        'ix_messages_community',
        'messages',
        ['community_id', sa.literal_column('id DESC')],
        unique=False,
    )
    op.create_index(
        'ix_messages_to', 'messages', ['to_id', sa.literal_column('id DESC')], unique=False
    )
    op.create_table(
        'mentions',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('comment_id', sa.BigInteger(), nullable=False),
        sa.Column('read', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column(
            'created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['account_id'], ['accounts.id'], name=op.f('fk_mentions_account_id_accounts')
        ),
        sa.ForeignKeyConstraint(
            ['comment_id'], ['comments.id'], name=op.f('fk_mentions_comment_id_comments')
        ),
        sa.PrimaryKeyConstraint('account_id', 'comment_id', name=op.f('pk_mentions')),
    )
    op.create_index(
        'ix_mentions_account',
        'mentions',
        ['account_id', sa.literal_column('created DESC')],
        unique=False,
    )
    op.create_index('ix_comments_parent', 'comments', ['parent_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_comments_parent', table_name='comments')
    op.drop_index('ix_mentions_account', table_name='mentions')
    op.drop_table('mentions')
    op.drop_index('ix_messages_to', table_name='messages')
    op.drop_index('ix_messages_community', table_name='messages')
    op.drop_index('ix_messages_from', table_name='messages')
    op.drop_index('ix_messages_first', table_name='messages')
    op.drop_table('messages')
