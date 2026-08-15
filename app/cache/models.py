"""Transport-neutral contracts for stateless research-result caching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

CacheTier = Literal["exact", "semantic", "miss"]


@dataclass(frozen=True, slots=True)
class CacheNamespace:
    """Version inputs that must agree before two results may be reused."""

    model: str
    prompt_version: str
    graph_version: str
    tool_schema_version: str
    policy_version: str = "1"
    embedding_model: str = "none"
    embedding_dimensions: int = 0

    @property
    def value(self) -> str:
        """Return a stable, human-inspectable namespace string."""
        parts = (
            self.model,
            self.prompt_version,
            self.graph_version,
            self.tool_schema_version,
            self.policy_version,
            self.embedding_model,
            str(self.embedding_dimensions),
        )
        return "|".join(part.strip().replace("|", "%7C") for part in parts)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    """Canonical request identity plus conservative semantic constraints."""

    original_query: str
    normalized_query: str
    query_hash: str
    exact_key: str
    namespace: str
    constraints: dict[str, object]
    semantic_eligible: bool
    semantic_text: str
    semantic_ineligibility_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CachePolicyDecision:
    """Whether a completed result is safe to cache and for how long."""

    cacheable: bool
    ttl_seconds: int
    expires_at: datetime | None
    source_retrieved_at: datetime | None
    artifact_types: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SemanticCacheHit:
    """Best unexpired, constraint-compatible vector-cache candidate."""

    payload: dict[str, object]
    similarity: float
    query_hash: str
    normalized_query: str
    expires_at: datetime
    source_retrieved_at: datetime | None


@dataclass(frozen=True, slots=True)
class CacheFailure:
    """Safe diagnostic for a cache operation that failed open."""

    tier: Literal["exact", "semantic", "embedding"]
    operation: Literal["get", "set", "lookup", "put", "embed"]
    error_type: str


@dataclass(frozen=True, slots=True)
class CacheLookup:
    """Outcome of an exact-then-semantic cache lookup."""

    tier: CacheTier
    payload: dict[str, object] | None
    similarity: float | None = None
    failures: tuple[CacheFailure, ...] = ()

    @property
    def hit(self) -> bool:
        """Return whether lookup avoided an origin research run."""
        return self.payload is not None and self.tier != "miss"


@dataclass(frozen=True, slots=True)
class CacheWriteResult:
    """Independent exact and semantic write outcomes."""

    exact_written: bool
    semantic_written: bool
    failures: tuple[CacheFailure, ...] = ()
