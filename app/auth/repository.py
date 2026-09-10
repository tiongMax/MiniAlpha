"""Account persistence protocol and controlled conflicts."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.auth.models import Account, AccountCredentials


class AccountPersistenceError(RuntimeError):
    """Base class for controlled account-storage failures."""


class EmailAlreadyRegisteredError(AccountPersistenceError):
    """Raised when a normalized email address already exists."""


class AccountRepository(Protocol):
    """Storage behavior required by the authentication service."""

    async def create_account(
        self, *, email: str, display_name: str, password_hash: str
    ) -> Account: ...

    async def get_credentials(self, email: str) -> AccountCredentials | None: ...

    async def create_session(
        self, *, user_id: UUID, token_hash: bytes, expires_at: datetime
    ) -> None: ...

    async def get_account_by_session(
        self, token_hash: bytes, *, now: datetime
    ) -> Account | None: ...

    async def delete_session(self, token_hash: bytes) -> None: ...
