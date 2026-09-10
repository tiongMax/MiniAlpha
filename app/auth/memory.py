"""Deterministic in-memory account repository for tests."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.auth.models import Account, AccountCredentials
from app.auth.repository import EmailAlreadyRegisteredError


class InMemoryAccountRepository:
    """Implement account and session storage without external services."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._credentials: dict[str, AccountCredentials] = {}
        self._sessions: dict[bytes, tuple[UUID, datetime]] = {}

    async def create_account(
        self, *, email: str, display_name: str, password_hash: str
    ) -> Account:
        async with self._lock:
            if email in self._credentials:
                raise EmailAlreadyRegisteredError("Email is already registered.")
            account = Account(
                user_id=uuid4(),
                email=email,
                display_name=display_name,
                created_at=datetime.now(UTC),
            )
            self._credentials[email] = AccountCredentials(account, password_hash)
            return account

    async def get_credentials(self, email: str) -> AccountCredentials | None:
        async with self._lock:
            return self._credentials.get(email)

    async def create_session(
        self, *, user_id: UUID, token_hash: bytes, expires_at: datetime
    ) -> None:
        async with self._lock:
            now = datetime.now(UTC)
            self._sessions = {
                token: session
                for token, session in self._sessions.items()
                if session[1] > now
            }
            self._sessions[token_hash] = (user_id, expires_at)

    async def get_account_by_session(
        self, token_hash: bytes, *, now: datetime
    ) -> Account | None:
        async with self._lock:
            stored = self._sessions.get(token_hash)
            if stored is None:
                return None
            user_id, expires_at = stored
            if expires_at <= now:
                del self._sessions[token_hash]
                return None
            return next(
                (
                    credentials.account
                    for credentials in self._credentials.values()
                    if credentials.account.user_id == user_id
                ),
                None,
            )

    async def delete_session(self, token_hash: bytes) -> None:
        async with self._lock:
            self._sessions.pop(token_hash, None)
