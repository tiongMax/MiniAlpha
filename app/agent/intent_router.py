"""Deterministic request router for request-scoped financial tool exposure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import HumanMessage

from app.agent.state import ResearchState
from app.agent.tool_registry import TOOL_NAMES_BY_GROUP, ToolRegistry

RoutingMode = Literal["intent", "no_tools", "fallback_all", "fixed_all"]


@dataclass(frozen=True, slots=True)
class IntentRule:
    """One explainable lexical signal mapped to an application intent group."""

    group: str
    patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True, slots=True)
class IntentRoute:
    """Inspectable routing decision stored in LangGraph state."""

    intents: tuple[str, ...]
    selected_tool_names: tuple[str, ...]
    mode: RoutingMode
    confidence: float
    reason: str

    def to_state(self) -> dict[str, object]:
        """Return the JSON-compatible representation persisted by LangGraph."""
        return {
            "intents": list(self.intents),
            "selected_tool_names": list(self.selected_tool_names),
            "mode": self.mode,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def _patterns(*values: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


INTENT_RULES: tuple[IntentRule, ...] = (
    IntentRule(
        "financial_statements",
        _patterns(
            r"\bfinancial statements?\b",
            r"\bincome statements?\b",
            r"\bbalance sheets?\b",
            r"\bcash[ -]?flows?\b",
            r"\brevenue\b",
            r"\bnet income\b",
        ),
    ),
    IntentRule(
        "fundamental_ratios",
        _patterns(
            r"\bvaluation\b",
            r"\bratios?\b",
            r"\bp/?e\b",
            r"\bprice[- ]to[- ]earnings\b",
            r"\bmargins?\b",
            r"\bprofitability\b",
        ),
    ),
    IntentRule(
        "analyst_estimates",
        _patterns(
            r"\bearnings estimates?\b",
            r"\banalyst estimates?\b",
            r"\bconsensus\b",
            r"\bprice targets?\b",
        ),
    ),
    IntentRule(
        "sec_filings",
        _patterns(r"\bsec\b", r"\b10-[kq]\b", r"\b8-k\b", r"\bfilings?\b"),
    ),
    IntentRule(
        "ownership",
        _patterns(
            r"\bownership\b",
            r"\bshareholders?\b",
            r"\binstitutional holders?\b",
        ),
    ),
    IntentRule(
        "insider_activity",
        _patterns(
            r"\binsiders?\b",
            r"\binsider (?:trades?|activity|transactions?)\b",
        ),
    ),
    IntentRule(
        "company_news",
        _patterns(r"\bnews\b", r"\bheadlines?\b", r"\brecent events?\b"),
    ),
    IntentRule(
        "correlation",
        _patterns(r"\bcorrelations?\b", r"\bcorrelated\b", r"\bco-movement\b"),
    ),
    IntentRule(
        "moving_average_backtest",
        _patterns(r"\bbacktests?\b", r"\bcrossover\b"),
    ),
    IntentRule(
        "technical_indicators",
        _patterns(
            r"\btechnical indicators?\b",
            r"\bsma\b",
            r"\bema\b",
            r"\brsi\b",
            r"\bmoving averages?\b",
        ),
    ),
    IntentRule(
        "return_statistics",
        _patterns(
            r"\breturns?\b",
            r"\bperformance\b",
            r"\bcagr\b",
        ),
    ),
    IntentRule(
        "volatility",
        _patterns(
            r"\bvolatilit(?:y|ies)\b",
            r"\brisk\b",
            r"\bstandard deviation\b",
            r"\bsharpe\b",
        ),
    ),
    IntentRule(
        "drawdown",
        _patterns(
            r"\bdrawdowns?\b",
            r"\bpeak[- ]to[- ]trough\b",
        ),
    ),
    IntentRule(
        "price_history",
        _patterns(
            r"\bprice history\b",
            r"\bhistorical prices?\b",
            r"\bprice charts?\b",
            r"\bprice trends?\b",
            r"\bclosing prices?\b",
            r"\bohlcv\b",
        ),
    ),
    IntentRule(
        "company_comparison",
        _patterns(
            r"\bcompare\b",
            r"\bcomparison\b",
            r"\bversus\b",
            r"\bvs\.?\b",
            r"\bside[- ]by[- ]side\b",
        ),
    ),
    IntentRule(
        "company_overview",
        _patterns(
            r"\boverviews?\b",
            r"\bprofiles?\b",
            r"\bsnapshots?\b",
            r"\banaly[sz]e\b",
            r"\bresearch\b",
        ),
    ),
)

CURRENT_DATA_SIGNALS = _patterns(
    r"\bcurrent\b",
    r"\blatest\b",
    r"\brecent\b",
    r"\btoday\b",
    r"\bshow\b",
    r"\bgive me\b",
    r"\bcalculate\b",
    r"\bcompare\b",
)

ENTITY_SIGNAL = re.compile(r"\b[A-Z][A-Za-z]+(?:['’]s)\b")
TICKER_SIGNAL = re.compile(r"\b[A-Z]{1,5}(?:-[A-Z])?(?:['’]s)?\b")

CONCEPTUAL_SIGNALS = _patterns(
    r"\bwhat (?:is|are|does)\b",
    r"\bexplain\b",
    r"\bdefine\b",
    r"\bhow (?:does|do|is|are)\b",
    r"\bconcept\b",
)

VAGUE_COMPANY_SIGNALS = _patterns(
    r"^\s*tell me about\s+[A-Z]{1,5}\.?\s*$",
    r"\bfinancially healthy\b",
    r"\bwhat should i know about\b",
    r"\blook into\b",
    r"\binvestigate\b",
    r"\buseful evidence (?:on|about)\b",
)

VAGUE_COMPANY_NAME = re.compile(
    r"\b(?:about|into|investigate|on)\s+(?:an?\s+)?[A-Z][A-Za-z]+\b"
)
HEALTH_SIGNALS = _patterns(r"\bfinancially healthy\b", r"\buseful evidence\b")
IMMEDIACY_SIGNALS = _patterns(r"\bright now\b")


class IntentRouter:
    """Map the latest user request to an explainable subset of registered tools."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def route(self, state: ResearchState) -> IntentRoute:
        """Route from the latest human message, including checkpointed turns."""
        text = _latest_user_text(state)
        financial = (
            any(pattern.search(text) for pattern in CURRENT_DATA_SIGNALS)
            or bool(ENTITY_SIGNAL.search(text))
            or bool(TICKER_SIGNAL.search(text))
        )
        conceptual = any(pattern.search(text) for pattern in CONCEPTUAL_SIGNALS)
        if conceptual and not financial:
            return IntentRoute(
                intents=(),
                selected_tool_names=(),
                mode="no_tools",
                confidence=0.9,
                reason="Conceptual request does not require current financial data.",
            )
        groups = tuple(
            rule.group
            for rule in INTENT_RULES
            if any(pattern.search(text) for pattern in rule.patterns)
        )
        if groups:
            selected = self._registry.names_for_groups(groups)
            if selected:
                return IntentRoute(
                    intents=groups,
                    selected_tool_names=selected,
                    mode="intent",
                    confidence=1.0 if len(groups) == 1 else 0.9,
                    reason="Matched explicit financial capability signals.",
                )

        vague_company_request = any(
            pattern.search(text) for pattern in VAGUE_COMPANY_SIGNALS
        ) and (
            bool(TICKER_SIGNAL.search(text)) or bool(VAGUE_COMPANY_NAME.search(text))
        )
        if vague_company_request:
            inferred_groups = ["company_overview"]
            if any(pattern.search(text) for pattern in HEALTH_SIGNALS):
                inferred_groups.append("fundamental_ratios")
            if any(pattern.search(text) for pattern in IMMEDIACY_SIGNALS):
                inferred_groups.append("company_news")
            selected = self._registry.names_for_groups(inferred_groups)
            return IntentRoute(
                intents=tuple(inferred_groups),
                selected_tool_names=selected,
                mode="intent",
                confidence=0.75,
                reason="Inferred a conservative evidence bundle for a named company.",
            )

        return IntentRoute(
            intents=("uncertain_financial",),
            selected_tool_names=self._registry.names,
            mode="fallback_all",
            confidence=0.0,
            reason="No safe narrow intent matched; exposed the complete toolset.",
        )


def _latest_user_text(state: ResearchState) -> str:
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def validate_intent_groups() -> None:
    """Fail fast when a rule references an undeclared application group."""
    unknown = {rule.group for rule in INTENT_RULES}.difference(TOOL_NAMES_BY_GROUP)
    if unknown:
        raise RuntimeError(f"Intent rules reference unknown groups: {sorted(unknown)}")


validate_intent_groups()
