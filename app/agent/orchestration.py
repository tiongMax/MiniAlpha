"""Deterministic contracts for research-tool orchestration.

The model can propose tool calls, but application code owns the supported
capabilities and validates arguments before a provider is invoked.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.errors import InvalidSymbolError
from app.services.company_research import normalize_symbol

TOOL_DESCRIPTION_VERSION = "orchestration-v1"
PRICE_PERIODS = frozenset({"1mo", "3mo", "6mo", "1y", "2y", "5y"})
PRICE_INTERVALS = frozenset({"1d", "1wk", "1mo"})
STATEMENT_FREQUENCIES = {
    "annual": "yearly",
    "annually": "yearly",
    "year": "yearly",
    "yearly": "yearly",
    "quarter": "quarterly",
    "quarters": "quarterly",
    "quarterly": "quarterly",
}


@dataclass(frozen=True, slots=True)
class ToolArgument:
    """One model-facing argument and its deterministic constraints."""

    name: str
    required: bool
    constraints: str


@dataclass(frozen=True, slots=True)
class ToolCapability:
    """Stable application-owned description of one financial tool."""

    name: str
    purpose: str
    exclusions: str
    arguments: tuple[ToolArgument, ...]
    output_artifact_type: str
    dependencies: tuple[str, ...]
    parallelizable: bool
    known_failure_modes: tuple[str, ...]
    recovery_options: tuple[str, ...]
    description_version: str = TOOL_DESCRIPTION_VERSION

    @property
    def required_arguments(self) -> tuple[str, ...]:
        """Return required argument names in declaration order."""
        return tuple(argument.name for argument in self.arguments if argument.required)


def _argument(name: str, constraints: str, *, required: bool = False) -> ToolArgument:
    return ToolArgument(name=name, required=required, constraints=constraints)


_SYMBOL = _argument(
    "symbol",
    "1-20 character public ticker; letters are normalized to uppercase",
    required=True,
)
_SYMBOLS = _argument("symbols", "2-5 distinct public tickers", required=True)
_PERIOD = _argument("period", "one of 1mo, 3mo, 6mo, 1y, 2y, or 5y")
_INTERVAL = _argument("interval", "one of 1d, 1wk, or 1mo")
_LIMIT = _argument("limit", "integer from 1 through 20")
_WINDOWS = (
    _argument("short_window", "integer from 2 through 199 and below long_window"),
    _argument("long_window", "integer from 3 through 200 and above short_window"),
)


def _capability(
    name: str,
    purpose: str,
    exclusions: str,
    output: str,
    arguments: tuple[ToolArgument, ...],
    *,
    dependencies: tuple[str, ...] = (),
    failure_modes: tuple[str, ...] = (
        "invalid_symbol",
        "missing_data",
        "provider_error",
    ),
    recovery: tuple[str, ...] = (
        "reject invalid arguments without retry",
        "return an explicit partial-result limitation when data is unavailable",
        "retry a transient provider failure only within the run retry budget",
    ),
) -> ToolCapability:
    return ToolCapability(
        name=name,
        purpose=purpose,
        exclusions=exclusions,
        arguments=arguments,
        output_artifact_type=output,
        dependencies=dependencies,
        parallelizable=True,
        known_failure_modes=failure_modes,
        recovery_options=recovery,
    )


TOOL_CAPABILITIES: Mapping[str, ToolCapability] = MappingProxyType(
    {
        item.name: item
        for item in (
            _capability(
                "get_company_overview",
                "Retrieve identity and a high-level company snapshot.",
                "Not for price-series analysis or detailed statements.",
                "company_overview",
                (_SYMBOL,),
            ),
            _capability(
                "get_price_history",
                "Retrieve historical OHLCV prices.",
                "Does not calculate risk, returns, indicators, or forecasts.",
                "price_history",
                (_SYMBOL, _PERIOD, _INTERVAL),
            ),
            _capability(
                "get_financial_statements",
                "Retrieve reported income, balance-sheet, and cash-flow periods.",
                "Not for ratios, estimates, or market-price history.",
                "financial_statements",
                (
                    _SYMBOL,
                    _argument("frequency", "yearly or quarterly, including aliases"),
                ),
            ),
            _capability(
                "get_fundamental_ratios",
                "Retrieve valuation, profitability, liquidity, and leverage ratios.",
                "Not for raw statements or analyst forecasts.",
                "fundamental_ratios",
                (_SYMBOL,),
            ),
            _capability(
                "get_analyst_estimates",
                "Retrieve forward-looking analyst consensus estimates.",
                "Must not be presented as reported company results.",
                "analyst_estimates",
                (_SYMBOL,),
            ),
            _capability(
                "get_sec_filings",
                "Retrieve recent SEC filing metadata and document links.",
                "Not for extracting unreturned filing contents.",
                "sec_filings",
                (_SYMBOL, _LIMIT),
            ),
            _capability(
                "get_ownership",
                "Retrieve aggregate ownership and institutional holders.",
                "Not for insider transactions.",
                "ownership",
                (_SYMBOL, _LIMIT),
            ),
            _capability(
                "get_insider_activity",
                "Retrieve reported insider transactions.",
                "Not for institutional ownership.",
                "insider_activity",
                (_SYMBOL, _LIMIT),
            ),
            _capability(
                "get_company_news",
                "Retrieve recent company headlines and source links.",
                "Headlines are not reported financial facts or forecasts.",
                "company_news",
                (_SYMBOL, _LIMIT),
            ),
            _capability(
                "compare_companies",
                "Compare normalized overview metrics for several companies.",
                "Not for time-series correlation or more than five symbols.",
                "company_comparison",
                (_SYMBOLS,),
            ),
            _capability(
                "calculate_return_statistics",
                "Calculate deterministic historical return statistics.",
                "Not for volatility, drawdowns, or forecasts.",
                "return_statistics",
                (_SYMBOL, _PERIOD, _INTERVAL),
                dependencies=("price_history",),
            ),
            _capability(
                "calculate_volatility",
                "Calculate deterministic historical volatility.",
                "Not for return performance or forward risk forecasts.",
                "volatility_analysis",
                (_SYMBOL, _PERIOD, _INTERVAL),
                dependencies=("price_history",),
            ),
            _capability(
                "analyze_drawdowns",
                "Calculate deterministic historical drawdowns.",
                "Not for volatility or forecasts.",
                "drawdown_analysis",
                (_SYMBOL, _PERIOD, _INTERVAL),
                dependencies=("price_history",),
            ),
            _capability(
                "calculate_correlations",
                "Calculate pairwise historical return correlations.",
                "Not for a single symbol or causal interpretation.",
                "correlation_analysis",
                (_SYMBOLS, _PERIOD, _INTERVAL),
                dependencies=("price_history",),
            ),
            _capability(
                "calculate_technical_indicators",
                "Calculate deterministic SMA, EMA, and RSI series.",
                "Indicators are historical measurements, not forecasts.",
                "technical_indicators",
                (
                    _SYMBOL,
                    _PERIOD,
                    _INTERVAL,
                    *_WINDOWS,
                    _argument("rsi_period", "integer from 2 through 50"),
                ),
                dependencies=("price_history",),
            ),
            _capability(
                "backtest_moving_average",
                "Run a deterministic lagged long/cash crossover backtest.",
                "Not for forecasting or non-moving-average strategies.",
                "moving_average_backtest",
                (
                    _SYMBOL,
                    _PERIOD,
                    _INTERVAL,
                    *_WINDOWS,
                    _argument("transaction_cost_bps", "number from 0 through 1000"),
                ),
                dependencies=("price_history",),
            ),
        )
    }
)


@dataclass(frozen=True, slots=True)
class ArgumentValidationIssue:
    """One actionable reason a proposed tool call cannot execute."""

    field: str
    code: str
    message: str
    recoverable: bool


@dataclass(frozen=True, slots=True)
class ToolArgumentValidation:
    """Normalized arguments and all deterministic validation issues."""

    tool_name: str
    arguments: Mapping[str, object]
    issues: tuple[ArgumentValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues


def _issue(
    field: str, code: str, message: str, *, recoverable: bool = True
) -> ArgumentValidationIssue:
    return ArgumentValidationIssue(field, code, message, recoverable)


def validate_tool_arguments(
    tool_name: str, arguments: Mapping[str, object]
) -> ToolArgumentValidation:
    """Validate and safely normalize one proposed tool call."""
    capability = TOOL_CAPABILITIES.get(tool_name)
    if capability is None:
        return ToolArgumentValidation(
            tool_name,
            MappingProxyType(dict(arguments)),
            (
                _issue(
                    "tool",
                    "unknown_tool",
                    f"Unsupported tool: {tool_name}.",
                    recoverable=False,
                ),
            ),
        )

    normalized = dict(arguments)
    issues: list[ArgumentValidationIssue] = []
    supported = {argument.name for argument in capability.arguments}
    for field in normalized.keys() - supported:
        issues.append(
            _issue(
                field, "unexpected_argument", f"{tool_name} does not accept {field}."
            )
        )
    for field in capability.required_arguments:
        if field not in normalized:
            issues.append(_issue(field, "missing_argument", f"{field} is required."))

    if "symbol" in normalized:
        value = normalized["symbol"]
        if not isinstance(value, str):
            issues.append(
                _issue("symbol", "invalid_symbol", "symbol must be a ticker string.")
            )
        else:
            try:
                normalized["symbol"] = normalize_symbol(value)
            except InvalidSymbolError as error:
                issues.append(_issue("symbol", "invalid_symbol", str(error)))

    if "symbols" in normalized:
        value = normalized["symbols"]
        symbols: list[str] = []
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            issues.append(
                _issue(
                    "symbols",
                    "invalid_symbols",
                    "symbols must be a list of ticker strings.",
                )
            )
        else:
            try:
                symbols = list(dict.fromkeys(normalize_symbol(item) for item in value))
            except InvalidSymbolError as error:
                issues.append(_issue("symbols", "invalid_symbol", str(error)))
            if symbols and not 2 <= len(symbols) <= 5:
                issues.append(
                    _issue(
                        "symbols", "symbol_count", "Use 2 to 5 distinct ticker symbols."
                    )
                )
            normalized["symbols"] = symbols

    for field, allowed in (("period", PRICE_PERIODS), ("interval", PRICE_INTERVALS)):
        if field not in normalized:
            continue
        value = normalized[field]
        if isinstance(value, str):
            normalized[field] = value.strip().lower()
        if normalized[field] not in allowed:
            choices = ", ".join(sorted(allowed))
            issues.append(
                _issue(field, f"invalid_{field}", f"Choose {field} from {choices}.")
            )

    if "frequency" in normalized:
        value = normalized["frequency"]
        frequency = (
            STATEMENT_FREQUENCIES.get(value.strip().lower())
            if isinstance(value, str)
            else None
        )
        if frequency is None:
            issues.append(
                _issue("frequency", "invalid_frequency", "Choose yearly or quarterly.")
            )
        else:
            normalized["frequency"] = frequency

    if "limit" in normalized:
        value = normalized["limit"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 20
        ):
            issues.append(
                _issue("limit", "invalid_limit", "Choose a result limit from 1 to 20.")
            )

    short = normalized.get("short_window", 20)
    long = normalized.get("long_window", 50)
    if "short_window" in normalized or "long_window" in normalized:
        if (
            isinstance(short, bool)
            or isinstance(long, bool)
            or not isinstance(short, int)
            or not isinstance(long, int)
            or not 2 <= short < long <= 200
        ):
            issues.append(
                _issue(
                    "short_window,long_window",
                    "invalid_windows",
                    "Use integer windows with 2 <= short_window < long_window <= 200.",
                )
            )

    if "rsi_period" in normalized:
        value = normalized["rsi_period"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 2 <= value <= 50
        ):
            issues.append(
                _issue(
                    "rsi_period",
                    "invalid_rsi_period",
                    "Use an RSI period from 2 to 50.",
                )
            )

    if "transaction_cost_bps" in normalized:
        value = normalized["transaction_cost_bps"]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 1_000
        ):
            issues.append(
                _issue(
                    "transaction_cost_bps",
                    "invalid_transaction_cost",
                    "Use transaction costs from 0 to 1000 basis points.",
                )
            )

    return ToolArgumentValidation(
        tool_name=tool_name,
        arguments=MappingProxyType(normalized),
        issues=tuple(issues),
    )


def validation_error_artifact(
    validation: ToolArgumentValidation,
) -> dict[str, object]:
    """Build the normal error-artifact envelope for a rejected call."""
    capability = TOOL_CAPABILITIES.get(validation.tool_name)
    return {
        "artifact_type": (
            capability.output_artifact_type if capability else "tool_validation_error"
        ),
        "schema_version": 1,
        "status": "error",
        "error": "Tool arguments failed deterministic validation.",
        "validation_issues": [
            {
                "field": issue.field,
                "code": issue.code,
                "message": issue.message,
                "recoverable": issue.recoverable,
            }
            for issue in validation.issues
        ],
    }


def validation_error_message(validation: ToolArgumentValidation) -> str:
    """Render concise repair guidance for the model."""
    return " ".join(issue.message for issue in validation.issues)
