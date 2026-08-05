"""Deterministic Phase 12 quantitative calculation and tool tests."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from math import isclose

import pytest
from langchain_core.messages import ToolMessage

from app.agent.tools import (
    create_correlation_tool,
    create_drawdown_tool,
    create_moving_average_backtest_tool,
    create_return_statistics_tool,
    create_technical_indicators_tool,
)
from app.domain.errors import InvalidQuantitativeQueryError
from app.domain.prices import PriceHistory, PricePoint
from app.services.quantitative_research import QuantitativeResearchService


def history(symbol: str, closes: list[float]) -> PriceHistory:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return PriceHistory(
        symbol=symbol,
        currency="USD",
        period="1y",
        interval="1d",
        points=tuple(
            PricePoint(
                timestamp=start + timedelta(days=index),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1_000,
            )
            for index, close in enumerate(closes)
        ),
        provider="Test Prices",
        retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
    )


class PriceServiceDouble:
    def __init__(self, histories: dict[str, PriceHistory]) -> None:
        self.histories = histories

    async def get_price_history(self, symbol, *, period, interval):
        result = self.histories[symbol.strip().upper()]
        return PriceHistory(
            symbol=result.symbol,
            currency=result.currency,
            period=period.strip().lower(),
            interval=interval.strip().lower(),
            points=result.points,
            provider=result.provider,
            retrieved_at=result.retrieved_at,
        )


def service(histories: dict[str, PriceHistory]) -> QuantitativeResearchService:
    return QuantitativeResearchService(PriceServiceDouble(histories))


def invoke(tool, args: dict[str, object]) -> ToolMessage:
    result = asyncio.run(
        tool.ainvoke(
            {"type": "tool_call", "id": "phase-12", "name": tool.name, "args": args}
        )
    )
    assert isinstance(result, ToolMessage)
    return result


def test_return_statistics_and_volatility_use_price_returns() -> None:
    research = service({"TEST": history("TEST", [100, 110, 99, 118.8])})

    returns = asyncio.run(research.return_statistics("TEST"))
    volatility = asyncio.run(research.volatility("TEST"))

    assert isclose(float(returns.summary["total_return"]), 0.188)
    assert isclose(float(returns.summary["arithmetic_mean_return"]), 0.2 / 3)
    assert returns.summary["best_period_return"] == pytest.approx(0.2)
    assert returns.summary["worst_period_return"] == pytest.approx(-0.1)
    assert returns.summary["positive_period_fraction"] == pytest.approx(2 / 3)
    assert volatility.summary["annualized_volatility"] == pytest.approx(
        0.15275252316519466 * (252**0.5)
    )
    assert len(returns.series) == 3


def test_calculations_prefer_adjusted_close_when_complete() -> None:
    raw = history("SPLIT", [100, 50, 55])
    adjusted = replace(
        raw,
        points=(
            replace(raw.points[0], adjusted_close=50),
            replace(raw.points[1], adjusted_close=50),
            replace(raw.points[2], adjusted_close=55),
        ),
    )

    result = asyncio.run(service({"SPLIT": adjusted}).return_statistics("SPLIT"))

    assert result.summary["total_return"] == pytest.approx(0.1)
    assert result.parameters["price_field"] == "adjusted_close"


def test_calculations_never_mix_partial_adjusted_and_raw_closes() -> None:
    raw = history("PARTIAL", [100, 50, 55])
    partial = replace(
        raw,
        points=(
            replace(raw.points[0], adjusted_close=50),
            replace(raw.points[1], adjusted_close=None),
            replace(raw.points[2], adjusted_close=55),
        ),
    )

    result = asyncio.run(service({"PARTIAL": partial}).return_statistics("PARTIAL"))

    assert result.summary["total_return"] == pytest.approx(-0.45)
    assert result.parameters["price_field"] == "close"


def test_drawdown_finds_peak_trough_recovery_and_current_level() -> None:
    research = service({"TEST": history("TEST", [100, 120, 90, 108, 121])})

    result = asyncio.run(research.drawdowns("TEST"))

    assert result.summary["maximum_drawdown"] == pytest.approx(-0.25)
    assert result.summary["current_drawdown"] == 0
    assert result.summary["peak_at"] == datetime(2026, 1, 2, tzinfo=UTC)
    assert result.summary["trough_at"] == datetime(2026, 1, 3, tzinfo=UTC)
    assert result.summary["recovered_at"] == datetime(2026, 1, 5, tzinfo=UTC)


def test_correlations_align_return_timestamps_and_bound_symbols() -> None:
    research = service(
        {
            "A": history("A", [100, 110, 99, 118.8]),
            "B": history("B", [100, 120, 96, 134.4]),
            "C": history("C", [100, 90, 99, 79.2]),
        }
    )

    result = asyncio.run(research.correlations([" a ", "B", "C", "A"]))
    matrix = result.summary["correlations"]

    assert result.symbols == ("A", "B", "C")
    assert matrix["A"]["B"] == pytest.approx(1)
    assert matrix["A"]["C"] == pytest.approx(-1)
    with pytest.raises(InvalidQuantitativeQueryError):
        asyncio.run(research.correlations(["A"]))


def test_correlations_align_prices_before_calculating_returns() -> None:
    first = history("A", [100, 110, 121, 133.1])
    second = history("B", [100, 120, 132])
    second = replace(
        second,
        points=(
            second.points[0],
            replace(second.points[1], timestamp=first.points[2].timestamp),
            replace(second.points[2], timestamp=first.points[3].timestamp),
        ),
    )

    result = asyncio.run(service({"A": first, "B": second}).correlations(["A", "B"]))

    assert result.summary["observations"]["A"]["B"] == 2
    assert result.summary["correlations"]["A"]["B"] == pytest.approx(1)
    assert (
        result.parameters["alignment"]
        == "common_price_dates_before_return_calculation"
    )


def test_downside_deviation_uses_root_mean_squared_zero_shortfall() -> None:
    research = service({"TEST": history("TEST", [100, 110, 99, 118.8])})

    result = asyncio.run(research.volatility("TEST"))

    expected = ((0.1**2) / 3) ** 0.5 * (252**0.5)
    assert result.summary["annualized_downside_deviation"] == pytest.approx(expected)
    assert result.parameters["minimum_acceptable_return"] == 0
    assert (
        result.parameters["downside_deviation_method"]
        == "root_mean_squared_shortfall"
    )


def test_indicators_and_backtest_are_deterministic_and_lag_signals() -> None:
    closes = [float(value) for value in range(1, 65)]
    research = service({"TEST": history("TEST", closes)})

    indicators = asyncio.run(
        research.technical_indicators(
            "TEST", short_window=2, long_window=3, rsi_period=2
        )
    )
    backtest = asyncio.run(
        research.backtest_moving_average(
            "TEST",
            short_window=2,
            long_window=3,
            transaction_cost_bps=10,
        )
    )

    assert indicators.summary["latest_sma_short"] == pytest.approx(63.5)
    assert indicators.summary["latest_sma_long"] == pytest.approx(63)
    assert indicators.summary["latest_rsi"] == 100
    assert indicators.summary["trend"] == "above"
    assert backtest.parameters["signal_lag_periods"] == 1
    assert backtest.series[2]["position"] == 0
    assert backtest.series[3]["position"] == 1
    assert backtest.series[3]["strategy_return"] == pytest.approx(
        (1 + 1 / 3) * 0.999 - 1
    )
    assert backtest.summary["trades"] == 1


def test_backtest_counts_and_charges_each_entry_and_exit() -> None:
    research = service({"TEST": history("TEST", [1, 2, 3, 2, 1, 2, 3])})

    result = asyncio.run(
        research.backtest_moving_average(
            "TEST",
            short_window=2,
            long_window=3,
            transaction_cost_bps=10,
        )
    )

    assert result.summary["trades"] == 2
    assert result.summary["entries"] == 1
    assert result.summary["exits"] == 1
    assert result.summary["ending_position"] == 0
    assert result.parameters["transaction_cost_basis"] == (
        "per_one_way_position_change"
    )
    assert result.series[3]["strategy_return"] == pytest.approx(
        (1 + (2 / 3 - 1)) * 0.999 - 1
    )
    assert result.series[5]["strategy_return"] == pytest.approx(-0.001)


def test_quantitative_tools_return_compact_content_and_structured_artifacts() -> None:
    research = service(
        {
            "A": history("A", [float(value) for value in range(100, 165)]),
            "B": history("B", [float(value) for value in range(200, 265)]),
        }
    )

    returns = invoke(create_return_statistics_tool(research), {"symbol": "A"})
    correlations = invoke(create_correlation_tool(research), {"symbols": ["A", "B"]})
    indicators = invoke(
        create_technical_indicators_tool(research),
        {"symbol": "A", "short_window": 5, "long_window": 20},
    )
    backtest = invoke(
        create_moving_average_backtest_tool(research),
        {"symbol": "A", "short_window": 5, "long_window": 20},
    )
    drawdowns = invoke(create_drawdown_tool(research), {"symbol": "A"})

    assert "deterministic historical calculation" in returns.content
    assert returns.artifact["artifact_type"] == "return_statistics"
    assert correlations.artifact["artifact_type"] == "correlation_analysis"
    assert indicators.artifact["artifact_type"] == "technical_indicators"
    assert backtest.artifact["data"]["parameters"]["signal_lag_periods"] == 1
    assert drawdowns.artifact["artifact_type"] == "drawdown_analysis"
    assert 'Peak At: "2026-' in drawdowns.content


@pytest.mark.parametrize(
    ("short_window", "long_window", "cost"),
    [(20, 20, 10), (1, 50, 10), (20, 201, 10), (20, 50, -1)],
)
def test_backtest_rejects_unsafe_parameters(
    short_window: int, long_window: int, cost: float
) -> None:
    research = service(
        {"TEST": history("TEST", [float(value) for value in range(1, 250)])}
    )

    with pytest.raises(InvalidQuantitativeQueryError):
        asyncio.run(
            research.backtest_moving_average(
                "TEST",
                short_window=short_window,
                long_window=long_window,
                transaction_cost_bps=cost,
            )
        )
