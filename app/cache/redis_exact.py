"""Redis-backed exact-match result cache and fill locks."""

import json
import secrets
from collections.abc import Mapping
from typing import cast

_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class RedisExactCache:
    """Store complete research results under precomputed exact cache keys.

    The adapter deliberately leaves cache-key construction and fail-open policy to
    the cache service. Values are JSON objects so deployments never depend on
    Python pickle compatibility or deserialize executable data.
    """

    def __init__(
        self,
        client: object,
        *,
        key_prefix: str = "mini-alpha:research-cache:exact",
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix.rstrip(":")

    @classmethod
    async def open(
        cls,
        redis_url: str,
        *,
        key_prefix: str = "mini-alpha:research-cache:exact",
    ) -> "RedisExactCache":
        """Connect to Redis and verify it before returning the adapter."""
        from redis.asyncio import Redis

        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        cache = cls(client, key_prefix=key_prefix)
        try:
            await client.ping()
            return cache
        except Exception:
            await client.aclose()
            raise

    def _key(self, key: str) -> str:
        normalized = key.strip()
        if not normalized:
            raise ValueError("Cache keys cannot be empty.")
        return f"{self._key_prefix}:{normalized}"

    def _lock_key(self, key: str) -> str:
        return f"{self._key(key)}:fill-lock"

    async def get(self, key: str) -> dict[str, object] | None:
        """Return a decoded cache object or ``None`` on a miss."""
        raw = await self._client.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("The exact-cache payload is not a JSON object.")
        return cast(dict[str, object], decoded)

    async def set(
        self,
        key: str,
        payload: Mapping[str, object],
        ttl_seconds: int,
    ) -> None:
        """Write a cache object with a mandatory finite TTL."""
        if ttl_seconds <= 0:
            raise ValueError("Exact-cache TTL must be positive.")
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        await self._client.set(
            self._key(key),
            encoded,
            ex=ttl_seconds,
        )

    async def delete(self, key: str) -> None:
        """Delete one exact entry."""
        await self._client.delete(self._key(key))

    async def acquire_fill_lock(
        self,
        key: str,
        *,
        ttl_seconds: int = 30,
    ) -> str | None:
        """Try to own an expiring single-flight lock for a cache miss.

        The opaque token must be supplied to :meth:`release_fill_lock`. A lock
        expiry prevents a crashed worker from permanently blocking the key.
        """
        if ttl_seconds <= 0:
            raise ValueError("Fill-lock TTL must be positive.")
        token = secrets.token_urlsafe(24)
        acquired = await self._client.set(
            self._lock_key(key),
            token,
            nx=True,
            ex=ttl_seconds,
        )
        return token if acquired else None

    async def release_fill_lock(self, key: str, token: str) -> bool:
        """Release a fill lock only when ``token`` still owns it."""
        if not token:
            return False
        removed = await self._client.eval(
            _RELEASE_LOCK_SCRIPT,
            1,
            self._lock_key(key),
            token,
        )
        return bool(removed)

    async def is_ready(self) -> bool:
        """Return whether Redis can currently serve cache commands."""
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        """Close the owned Redis client."""
        await self._client.aclose()
