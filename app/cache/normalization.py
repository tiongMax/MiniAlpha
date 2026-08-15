"""Deterministic query normalization and semantic-cache safety gates."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable

from app.cache.models import CacheNamespace, QueryFingerprint

_WHITESPACE = re.compile(r"\s+")
_DOLLAR_TICKER = re.compile(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,5}(?:-[A-Za-z])?)\b")
_BARE_TICKER = re.compile(r"(?<![A-Za-z0-9$])([A-Z]{1,5}(?:-[A-Z])?)\b")
_TIME_TOKEN = re.compile(
    r"\b(?:\d+\s*(?:d|day|days|wk|week|weeks|mo|month|months|y|yr|yrs|year|years)"
    r"|ytd|max|daily|weekly|monthly|quarterly|annual(?:ly)?)\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_CONTEXT_DEPENDENT = re.compile(
    r"\b(?:it|its|they|them|their|those|that company|same|above|previous|"
    r"former|latter|as before|again)\b",
    re.IGNORECASE,
)
_RELATIVE_TIME = re.compile(
    r"\b(?:today|now|currently|current|latest|recent|recently|yesterday|"
    r"tomorrow|this (?:week|month|quarter|year))\b",
    re.IGNORECASE,
)

# Upper-case financial vocabulary should not be mistaken for an explicit symbol.
_NON_TICKERS = frozenset(
    {
        "A",
        "AI",
        "AND",
        "ARE",
        "CAGR",
        "CEO",
        "DCF",
        "EMA",
        "EPS",
        "ETF",
        "FOR",
        "FROM",
        "HOW",
        "I",
        "IN",
        "IPO",
        "IS",
        "LATEST",
        "MAX",
        "MY",
        "NOW",
        "OF",
        "ON",
        "OR",
        "P",
        "PE",
        "RATIO",
        "ROA",
        "ROE",
        "RSI",
        "SEC",
        "SHOW",
        "SMA",
        "THE",
        "TO",
        "TODAY",
        "USD",
        "VS",
        "WHAT",
        "WITH",
        "YTD",
    }
)

_INTENT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("company_overview", ("overview", "profile", "snapshot", "research")),
    ("price_history", ("price history", "historical price", "price trend", "ohlcv")),
    (
        "financial_statements",
        ("income statement", "balance sheet", "cash flow", "revenue"),
    ),
    ("fundamental_ratios", ("valuation", "ratio", "p/e", "margin", "profitability")),
    ("analyst_estimates", ("analyst", "estimate", "consensus", "price target")),
    ("sec_filings", ("sec filing", "10-k", "10-q", "8-k")),
    ("ownership", ("ownership", "shareholder", "institutional holder")),
    ("insider_activity", ("insider",)),
    ("company_news", ("news", "headline")),
    ("company_comparison", ("compare", "comparison", " versus ", " vs ")),
    ("return_statistics", ("return", "performance", "cagr")),
    ("volatility", ("volatility", "sharpe", "standard deviation")),
    ("drawdown", ("drawdown", "peak-to-trough")),
    ("correlation", ("correlation", "correlated", "co-movement")),
    ("technical_indicators", ("technical indicator", "rsi", "sma", "ema")),
    ("moving_average_backtest", ("backtest", "crossover")),
)


def normalize_query(query: str) -> str:
    """Normalize Unicode, whitespace, and case without removing constraints."""
    normalized = unicodedata.normalize("NFKC", query)
    return _WHITESPACE.sub(" ", normalized).strip().casefold()


def fingerprint_query(
    query: str,
    namespace: str | CacheNamespace,
    *,
    intents: Iterable[str] | None = None,
) -> QueryFingerprint:
    """Build an exact key and a guarded semantic-search fingerprint."""
    normalized = normalize_query(query)
    namespace_value = str(namespace)
    query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    exact_digest = hashlib.sha256(
        f"{namespace_value}\0{normalized}".encode()
    ).hexdigest()

    symbols = _extract_symbols(query)
    normalized_intents = (
        _normalize_intents(intents)
        if intents is not None
        else _infer_intents(normalized)
    )
    time_tokens = tuple(
        sorted(
            {
                _normalize_time_token(match.group(0))
                for match in _TIME_TOKEN.finditer(query)
            }
        )
    )
    dates = tuple(sorted(set(_ISO_DATE.findall(query))))
    constraints: dict[str, object] = {
        "symbols": list(symbols),
        "intents": list(normalized_intents),
        "time_tokens": list(time_tokens),
        "dates": list(dates),
    }

    reasons: list[str] = []
    if not symbols:
        reasons.append("missing_explicit_ticker")
    if not normalized_intents:
        reasons.append("missing_intent")
    if "uncertain_financial" in normalized_intents or "fixed_all" in normalized_intents:
        reasons.append("unsafe_intent")
    if _CONTEXT_DEPENDENT.search(query):
        reasons.append("context_dependent_language")
    if _RELATIVE_TIME.search(query):
        reasons.append("relative_time_language")

    # Including the exact constraints in the embedding input reinforces the
    # same checks performed structurally by the semantic store.
    semantic_text = json.dumps(
        {"query": normalized, "constraints": constraints},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return QueryFingerprint(
        original_query=query,
        normalized_query=normalized,
        query_hash=query_hash,
        exact_key=f"mini-alpha:research:{exact_digest}",
        namespace=namespace_value,
        constraints=constraints,
        semantic_eligible=not reasons,
        semantic_text=semantic_text,
        semantic_ineligibility_reasons=tuple(reasons),
    )


def _extract_symbols(query: str) -> tuple[str, ...]:
    symbols = {match.group(1).upper() for match in _DOLLAR_TICKER.finditer(query)}
    symbols.update(
        symbol
        for match in _BARE_TICKER.finditer(query)
        if (symbol := match.group(1).upper()) not in _NON_TICKERS
    )
    return tuple(sorted(symbols))


def _normalize_intents(intents: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted({intent.strip().casefold() for intent in intents if intent.strip()})
    )


def _infer_intents(normalized_query: str) -> tuple[str, ...]:
    padded = f" {normalized_query} "
    return tuple(
        sorted(
            intent
            for intent, terms in _INTENT_TERMS
            if any(term in padded for term in terms)
        )
    )


def _normalize_time_token(value: str) -> str:
    value = _WHITESPACE.sub("", value).casefold()
    replacements = {
        "days": "d",
        "day": "d",
        "weeks": "wk",
        "week": "wk",
        "months": "mo",
        "month": "mo",
        "years": "y",
        "year": "y",
        "yrs": "y",
        "yr": "y",
        "daily": "1d",
        "weekly": "1wk",
        "monthly": "1mo",
        "quarterly": "quarterly",
        "annually": "annual",
    }
    for suffix, replacement in replacements.items():
        if value.endswith(suffix):
            prefix = value[: -len(suffix)]
            return f"{prefix or ''}{replacement}"
    return value
