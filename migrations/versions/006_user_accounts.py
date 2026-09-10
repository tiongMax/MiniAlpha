"""Add user accounts and opaque authentication sessions.

Revision ID: 006_user_accounts
Revises: 005_structured_failures
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "006_user_accounts"
down_revision: str | Sequence[str] | None = "005_structured_failures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create account identities and revocable hashed sessions."""
    op.execute(
        """
        CREATE TABLE auth_users (
            user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(254) NOT NULL UNIQUE
                CHECK (email = LOWER(email) AND LENGTH(BTRIM(email)) > 3),
            display_name VARCHAR(80) NOT NULL
                CHECK (LENGTH(BTRIM(display_name)) BETWEEN 1 AND 80),
            password_hash TEXT NOT NULL CHECK (LENGTH(password_hash) > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE auth_sessions (
            session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES auth_users(user_id) ON DELETE CASCADE,
            token_hash BYTEA NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_auth_sessions_user
        ON auth_sessions (user_id, expires_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_auth_sessions_expiry
        ON auth_sessions (expires_at)
        """
    )


def downgrade() -> None:
    """Remove account and session records."""
    op.execute("DROP TABLE auth_sessions")
    op.execute("DROP TABLE auth_users")
