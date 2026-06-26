"""init

Revision ID: be5f55e6e768
Revises:
Create Date: 2026-06-26 10:13:47.123052

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "be5f55e6e768"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
