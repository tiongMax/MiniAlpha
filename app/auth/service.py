"""Account registration and opaque session authentication."""

import asyncio
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from app.auth.models import Account
from app.auth.passwords import hash_password, verify_password
from app.auth.repository import AccountRepository

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = hash_password("not-a-real-user-password")


class InvalidCredentialsError(RuntimeError):
    """Raised without revealing whether an email address exists."""


class AuthenticationRequiredError(RuntimeError):
    """Raised when a protected operation has no valid session."""


def normalize_email(email: str) -> str:
    """Normalize and validate an email identity."""
    normalized = email.strip().casefold()
    if len(normalized) > 254 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Enter a valid email address.")
    return normalized


def normalize_display_name(display_name: str) -> str:
    """Normalize a compact user-facing account name."""
    normalized = " ".join(display_name.split())
    if not 1 <= len(normalized) <= 80:
        raise ValueError("Display name must contain between 1 and 80 characters.")
    return normalized


def validate_password(password: str) -> str:
    """Enforce a bounded password without silently changing it."""
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain between 12 and 128 characters.")
    return password


class AuthService:
    """Coordinate account persistence and rotating opaque sessions."""

    def __init__(self, repository: AccountRepository, *, session_ttl_seconds: int):
        self._repository = repository
        self.session_ttl_seconds = session_ttl_seconds

    async def register(
        self, *, email: str, display_name: str, password: str
    ) -> tuple[Account, str]:
        password_hash = await asyncio.to_thread(
            hash_password, validate_password(password)
        )
        account = await self._repository.create_account(
            email=normalize_email(email),
            display_name=normalize_display_name(display_name),
            password_hash=password_hash,
        )
        return account, await self._create_session(account)

    async def login(self, *, email: str, password: str) -> tuple[Account, str]:
        normalized_email = normalize_email(email)
        validate_password(password)
        credentials = await self._repository.get_credentials(normalized_email)
        encoded = credentials.password_hash if credentials is not None else _DUMMY_HASH
        valid = await asyncio.to_thread(verify_password, password, encoded)
        if credentials is None or not valid:
            raise InvalidCredentialsError("Email or password is incorrect.")
        return credentials.account, await self._create_session(credentials.account)

    async def resolve_session(self, token: str | None) -> Account | None:
        if not token:
            return None
        return await self._repository.get_account_by_session(
            self._token_hash(token), now=datetime.now(UTC)
        )

    async def logout(self, token: str | None) -> None:
        if token:
            await self._repository.delete_session(self._token_hash(token))

    async def _create_session(self, account: Account) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=self.session_ttl_seconds)
        await self._repository.create_session(
            user_id=account.user_id,
            token_hash=self._token_hash(token),
            expires_at=expires_at,
        )
        return token

    @staticmethod
    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()
