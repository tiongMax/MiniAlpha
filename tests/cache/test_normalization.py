"""Query fingerprints prevent unsafe cross-request cache reuse."""

from app.cache.models import CacheNamespace
from app.cache.normalization import fingerprint_query, normalize_query


def test_exact_normalization_is_unicode_case_and_whitespace_stable() -> None:
    namespace = CacheNamespace(
        model="gemini-2.5-flash",
        prompt_version="p1",
        graph_version="g1",
        tool_schema_version="t1",
    )

    first = fingerprint_query("  SHOW\u3000$AAPL  OVERVIEW ", namespace)
    second = fingerprint_query("show $aapl overview", namespace)

    assert normalize_query("  SHOW\u3000$AAPL  ") == "show $aapl"
    assert first.query_hash == second.query_hash
    assert first.exact_key == second.exact_key
    assert first.namespace == namespace.value


def test_namespace_versions_invalidate_exact_entries() -> None:
    first = fingerprint_query("Show AAPL overview", "model-a|prompt-1")
    second = fingerprint_query("Show AAPL overview", "model-a|prompt-2")

    assert first.query_hash == second.query_hash
    assert first.exact_key != second.exact_key


def test_semantic_fingerprint_extracts_required_financial_constraints() -> None:
    fingerprint = fingerprint_query(
        "Compare AAPL and MSFT volatility over 1 year",
        "test",
    )

    assert fingerprint.semantic_eligible
    assert fingerprint.constraints == {
        "symbols": ["AAPL", "MSFT"],
        "intents": ["company_comparison", "volatility"],
        "time_tokens": ["1y"],
        "dates": [],
    }
    assert fingerprint.semantic_ineligibility_reasons == ()


def test_semantic_cache_rejects_contextual_or_time_relative_requests() -> None:
    contextual = fingerprint_query(
        "Compare its volatility with MSFT over 1 year",
        "test",
    )
    relative = fingerprint_query("Show the latest AAPL news", "test")

    assert not contextual.semantic_eligible
    assert "context_dependent_language" in contextual.semantic_ineligibility_reasons
    assert not relative.semantic_eligible
    assert "relative_time_language" in relative.semantic_ineligibility_reasons


def test_semantic_cache_requires_explicit_ticker_and_known_intent() -> None:
    company_name_only = fingerprint_query("Show Apple volatility", "test")
    unknown_intent = fingerprint_query("Tell me something about AAPL", "test")

    assert not company_name_only.semantic_eligible
    assert "missing_explicit_ticker" in company_name_only.semantic_ineligibility_reasons
    assert not unknown_intent.semantic_eligible
    assert "missing_intent" in unknown_intent.semantic_ineligibility_reasons


def test_financial_acronyms_are_not_misclassified_as_tickers() -> None:
    fingerprint = fingerprint_query("Explain SEC filings and RSI", "test")

    assert fingerprint.constraints["symbols"] == []
    assert not fingerprint.semantic_eligible


def test_caller_supplied_intents_override_lexical_inference() -> None:
    fingerprint = fingerprint_query(
        "Inspect AAPL",
        "test",
        intents=("company_overview",),
    )

    assert fingerprint.semantic_eligible
    assert fingerprint.constraints["intents"] == ["company_overview"]
