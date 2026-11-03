"""rename the media pref value

Revision ID: b7afcc599d3c
Revises: 90a7bf41452e
Create Date: 2026-11-02 13:18:43.457188
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = 'b7afcc599d3c'
down_revision: str | Sequence[str] | None = '90a7bf41452e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIELDS = ('media', 'media_preview')


def _rename(old: str, new: str) -> None:
    for field in FIELDS:
        op.execute(
            sa.text(
                'update accounts set prefs = jsonb_set(prefs, array[:field], to_jsonb(:new)) '
                'where prefs->>:field = :old'
            ).bindparams(field=field, new=new, old=old)
        )


def upgrade() -> None:
    _rename('subreddit', 'community')


def downgrade() -> None:
    _rename('community', 'subreddit')
