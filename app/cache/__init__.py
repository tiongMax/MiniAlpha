"""Exact and semantic result caching for stateless research requests."""

from app.cache.models import (
    CacheFailure,
    CacheLookup,
    CacheNamespace,
    CachePolicyDecision,
    CacheWriteResult,
    QueryFingerprint,
    SemanticCacheHit,
)
from app.cache.normalization import fingerprint_query, normalize_query
from app.cache.policy import ARTIFACT_TTL_SECONDS, evaluate_artifact_ttl
from app.cache.service import ResearchResultCacheService
from app.cache.stores import (
    CacheCoordinator,
    Embedder,
    ExactCacheStore,
    InMemoryExactCache,
    InMemorySemanticCache,
    SemanticCacheStore,
)

__all__ = [
    "ARTIFACT_TTL_SECONDS",
    "CacheCoordinator",
    "CacheFailure",
    "CacheLookup",
    "CacheNamespace",
    "CachePolicyDecision",
    "CacheWriteResult",
    "Embedder",
    "ExactCacheStore",
    "InMemoryExactCache",
    "InMemorySemanticCache",
    "QueryFingerprint",
    "ResearchResultCacheService",
    "SemanticCacheHit",
    "SemanticCacheStore",
    "evaluate_artifact_ttl",
    "fingerprint_query",
    "normalize_query",
]
