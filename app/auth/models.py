"""Transport-neutral authentication records."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Account:
    """Public account identity safe to expose to application code."""

    user_id: UUID
    email: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AccountCredentials:
    """Account identity plus its private password verifier."""

    account: Account
    password_hash: str
