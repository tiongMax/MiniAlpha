"""Persist artifact provenance alongside stable artifact identity.

Revision ID: 004_grounding_provenance
Revises: 003_phase_7_partial_cancel
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004_grounding_provenance"
down_revision: str | Sequence[str] | None = "003_phase_7_partial_cancel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add optional provenance; existing artifacts remain explicitly legacy."""
    op.execute("ALTER TABLE conversation_artifacts ADD COLUMN provenance JSONB")


def downgrade() -> None:
    """Remove persisted provenance without changing artifact data."""
    op.execute("ALTER TABLE conversation_artifacts DROP COLUMN provenance")
