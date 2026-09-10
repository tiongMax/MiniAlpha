"""PostgreSQL account and session repository."""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection, errors
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool

from app.auth.models import Account, AccountCredentials
from app.auth.repository import EmailAlreadyRegisteredError


def _account(row: DictRow) -> Account:
    return Account(
        user_id=row["user_id"],
        email=row["email"],
        display_name=row["display_name"],
        created_at=row["created_at"],
    )


class PostgresAccountRepository:
    """Persist normalized accounts and hashed opaque sessions."""

    def __init__(self, pool: AsyncConnectionPool[AsyncConnection[DictRow]]) -> None:
        self._pool = pool

    async def create_account(
        self, *, email: str, display_name: str, password_hash: str
    ) -> Account:
        try:
            async with self._pool.connection() as connection:
                row = await connection.execute(
                    """
                    INSERT INTO auth_users (email, display_name, password_hash)
                    VALUES (%s, %s, %s)
                    RETURNING user_id, email, display_name, created_at
                    """,
                    (email, display_name, password_hash),
                )
                stored = await row.fetchone()
        except errors.UniqueViolation as error:
            raise EmailAlreadyRegisteredError(
                "Email is already registered."
            ) from error
        if stored is None:
            raise RuntimeError("Account insert did not return a record.")
        return _account(stored)

    async def get_credentials(self, email: str) -> AccountCredentials | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT user_id, email, display_name, password_hash, created_at
                FROM auth_users
                WHERE email = %s
                """,
                (email,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return AccountCredentials(_account(row), row["password_hash"])

    async def create_session(
        self, *, user_id: UUID, token_hash: bytes, expires_at: datetime
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= NOW()"
            )
            await connection.execute(
                """
                INSERT INTO auth_sessions (user_id, token_hash, expires_at)
                VALUES (%s, %s, %s)
                """,
                (user_id, token_hash, expires_at),
            )

    async def get_account_by_session(
        self, token_hash: bytes, *, now: datetime
    ) -> Account | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT users.user_id, users.email, users.display_name, users.created_at
                FROM auth_sessions AS sessions
                JOIN auth_users AS users USING (user_id)
                WHERE sessions.token_hash = %s AND sessions.expires_at > %s
                """,
                (token_hash, now),
            )
            row = await cursor.fetchone()
        return _account(row) if row is not None else None

    async def delete_session(self, token_hash: bytes) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = %s",
                (token_hash,),
            )
