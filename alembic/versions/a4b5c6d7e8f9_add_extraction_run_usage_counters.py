"""add provider usage counters to extraction_runs

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-27

A5f Pass 2B1: per-run provider accounting so token spend, cache effectiveness,
and latency are auditable without re-running extraction.

Counters only. The prompt, the source-page content, and the model's raw
response are deliberately not persisted here or anywhere else.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COUNTER_COLUMNS = (
    "provider_call_count",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "duration_ms",
)


def upgrade() -> None:
    for column in _COUNTER_COLUMNS:
        op.add_column(
            "extraction_runs",
            sa.Column(column, sa.Integer(), server_default="0", nullable=False),
        )


def downgrade() -> None:
    for column in reversed(_COUNTER_COLUMNS):
        op.drop_column("extraction_runs", column)
