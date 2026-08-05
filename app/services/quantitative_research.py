"""Deterministic calculations over normalized historical prices."""

import asyncio
from datetime import UTC, datetime
from math import sqrt
from statistics import fmean, stdev

from app.domain.errors import InvalidQuantitativeQueryError
from app.domain.prices import PriceHistory
from app.domain.quantitative import QuantitativeDataset
from app.services.company_research import CompanyResearchService, normalize_symbol

_PERIODS_PER_YEAR = {"1d": 252, "1wk": 52, "1mo": 12}


def _price_field(history: PriceHistory) -> str:
    return (
        "adjusted_close"
        if all(point.adjusted_close is not None for point in history.points)
        else "close"
    )


def _analysis_prices(history: PriceHistory) -> list[float]:
    """Select one consistent price field for the complete history."""
    if _price_field(history) == "adjusted_close":
        return [float(point.adjusted_close) for point in history.points]
    return [point.close for point in history.points]


def _require_observations(history: PriceHistory, minimum: int) -> None:
    if len(history.points) < minimum:
        raise InvalidQuantitativeQueryError(
            f"{history.symbol} needs at least {minimum} price observations "
            "for this calculation."
        )
    if any(price <= 0 for price in _analysis_prices(history)):
        raise InvalidQuantitativeQueryError(
            f"{history.symbol} contains a non-positive closing price."
        )


def _simple_returns(history: PriceHistory) -> list[tuple[datetime, float]]:
    _require_observations(history, 2)
    prices = _analysis_prices(history)
    return [
        (
            current.timestamp,
            current_price / previous_price - 1,
        )
        for previous_price, current_price, current in zip(
            prices[:-1], prices[1:], history.points[1:], strict=True
        )
    ]


def _annualized_return(total_return: float, observations: int, periods: int) -> float:
    if observations <= 0 or total_return <= -1:
        return -1.0 if total_return <= -1 else 0.0
    return (1 + total_return) ** (periods / observations) - 1


def _sample_volatility(returns: list[float], periods: int) -> float:
    return stdev(returns) * sqrt(periods) if len(returns) >= 2 else 0.0


def _drawdown_records(
    timestamps: list[datetime], values: list[float]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    peak_value = values[0]
    peak_index = 0
    worst = 0.0
    worst_peak_index = 0
    trough_index = 0
    records: list[dict[str, object]] = []
    for index, (timestamp, value) in enumerate(zip(timestamps, values, strict=True)):
        if value > peak_value:
            peak_value = value
            peak_index = index
        drawdown = value / peak_value - 1
        records.append(
            {
                "timestamp": timestamp,
                "value": value,
                "drawdown": drawdown,
            }
        )
        if drawdown < worst:
            worst = drawdown
            worst_peak_index = peak_index
            trough_index = index

    recovery_index: int | None = None
    recovery_level = values[worst_peak_index]
    for index in range(trough_index + 1, len(values)):
        if values[index] >= recovery_level:
            recovery_index = index
            break
    squared = [float(record["drawdown"]) ** 2 for record in records]
    summary: dict[str, object] = {
        "maximum_drawdown": worst,
        "peak_at": timestamps[worst_peak_index],
        "trough_at": timestamps[trough_index],
        "recovered_at": (
            timestamps[recovery_index] if recovery_index is not None else None
        ),
        "current_drawdown": float(records[-1]["drawdown"]),
        "ulcer_index": sqrt(fmean(squared)),
    }
    return records, summary


def _sma(values: list[float], window: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    rolling = 0.0
    for index, value in enumerate(values):
        rolling += value
        if index >= window:
            rolling -= values[index - window]
        if index >= window - 1:
            output[index] = rolling / window
    return output


def _ema(values: list[float], window: int) -> list[float]:
    multiplier = 2 / (window + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(value * multiplier + output[-1] * (1 - multiplier))
    return output


def _rsi(values: list[float], period: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    changes = [
        current - previous
        for previous, current in zip(values, values[1:], strict=False)
    ]
    if len(changes) < period:
        return output
    average_gain = fmean(max(change, 0.0) for change in changes[:period])
    average_loss = fmean(max(-change, 0.0) for change in changes[:period])

    def value(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100 - 100 / (1 + gain / loss)

    output[period] = value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        output[index] = value(average_gain, average_loss)
    return output


class QuantitativeResearchService:
    """Fetch normalized prices and perform deterministic application math."""

    def __init__(self, company_service: CompanyResearchService) -> None:
        self._companies = company_service

    async def _history(
        self, symbol: str, *, period: str, interval: str
    ) -> PriceHistory:
        return await self._companies.get_price_history(
            symbol, period=period, interval=interval
        )

    @staticmethod
    def _dataset(
        history: PriceHistory,
        *,
        analysis: str,
        parameters: dict[str, object],
        summary: dict[str, object],
        series: list[dict[str, object]],
    ) -> QuantitativeDataset:
        return QuantitativeDataset(
            analysis=analysis,
            symbols=(history.symbol,),
            period=history.period,
            interval=history.interval,
            parameters=parameters,
            summary=summary,
            series=tuple(series),
            provider=history.provider,
            source_retrieved_at=history.retrieved_at,
            calculated_at=datetime.now(UTC),
        )

    async def return_statistics(
        self, symbol: str, *, period: str = "1y", interval: str = "1d"
    ) -> QuantitativeDataset:
        history = await self._history(symbol, period=period, interval=interval)
        returns = _simple_returns(history)
        values = [value for _, value in returns]
        annualization = _PERIODS_PER_YEAR[history.interval]
        prices = _analysis_prices(history)
        total_return = (
            prices[-1] / prices[0] - 1
        )
        summary: dict[str, object] = {
            "observations": len(history.points),
            "return_observations": len(values),
            "total_return": total_return,
            "annualized_return": _annualized_return(
                total_return, len(values), annualization
            ),
            "arithmetic_mean_return": fmean(values),
            "best_period_return": max(values),
            "worst_period_return": min(values),
            "positive_period_fraction": sum(value > 0 for value in values)
            / len(values),
        }
        series = [
            {"timestamp": timestamp, "return": value} for timestamp, value in returns
        ]
        return self._dataset(
            history,
            analysis="return_statistics",
            parameters={
                "annualization_periods": annualization,
                "price_field": _price_field(history),
            },
            summary=summary,
            series=series,
        )

    async def volatility(
        self, symbol: str, *, period: str = "1y", interval: str = "1d"
    ) -> QuantitativeDataset:
        history = await self._history(symbol, period=period, interval=interval)
        returns = _simple_returns(history)
        values = [value for _, value in returns]
        if len(values) < 2:
            raise InvalidQuantitativeQueryError(
                "Volatility requires at least two return observations."
            )
        annualization = _PERIODS_PER_YEAR[history.interval]
        downside = [min(value, 0.0) for value in values]
        summary: dict[str, object] = {
            "observations": len(values),
            "period_volatility": stdev(values),
            "annualized_volatility": _sample_volatility(values, annualization),
            "annualized_downside_deviation": sqrt(
                fmean(value * value for value in downside)
            )
            * sqrt(annualization),
        }
        return self._dataset(
            history,
            analysis="volatility_analysis",
            parameters={
                "annualization_periods": annualization,
                "minimum_acceptable_return": 0.0,
                "downside_deviation_method": "root_mean_squared_shortfall",
                "price_field": _price_field(history),
            },
            summary=summary,
            series=[
                {"timestamp": timestamp, "return": value}
                for timestamp, value in returns
            ],
        )

    async def drawdowns(
        self, symbol: str, *, period: str = "2y", interval: str = "1d"
    ) -> QuantitativeDataset:
        history = await self._history(symbol, period=period, interval=interval)
        _require_observations(history, 2)
        records, summary = _drawdown_records(
            [point.timestamp for point in history.points],
            _analysis_prices(history),
        )
        summary["observations"] = len(history.points)
        return self._dataset(
            history,
            analysis="drawdown_analysis",
            parameters={"price_field": _price_field(history)},
            summary=summary,
            series=records,
        )

    async def correlations(
        self,
        symbols: list[str],
        *,
        period: str = "1y",
        interval: str = "1d",
    ) -> QuantitativeDataset:
        normalized = list(dict.fromkeys(normalize_symbol(item) for item in symbols))
        if not 2 <= len(normalized) <= 5:
            raise InvalidQuantitativeQueryError(
                "Calculate correlations for 2 to 5 distinct ticker symbols."
            )
        histories = await asyncio.gather(
            *(
                self._history(symbol, period=period, interval=interval)
                for symbol in normalized
            )
        )
        for history in histories:
            _require_observations(history, 2)
        price_series = {
            history.symbol: {
                point.timestamp.date(): price
                for point, price in zip(
                    history.points, _analysis_prices(history), strict=True
                )
            }
            for history in histories
        }
        matrix: dict[str, dict[str, float | None]] = {
            symbol: {} for symbol in normalized
        }
        observations: dict[str, dict[str, int]] = {symbol: {} for symbol in normalized}
        for left in normalized:
            for right in normalized:
                common = sorted(
                    price_series[left].keys() & price_series[right].keys()
                )
                left_prices = [price_series[left][timestamp] for timestamp in common]
                right_prices = [
                    price_series[right][timestamp] for timestamp in common
                ]
                left_values = [
                    current / previous - 1
                    for previous, current in zip(
                        left_prices, left_prices[1:], strict=False
                    )
                ]
                right_values = [
                    current / previous - 1
                    for previous, current in zip(
                        right_prices, right_prices[1:], strict=False
                    )
                ]
                observations[left][right] = len(left_values)
                if left == right and left_values:
                    correlation: float | None = 1.0
                elif len(common) < 2:
                    correlation = None
                else:
                    left_mean = fmean(left_values)
                    right_mean = fmean(right_values)
                    numerator = sum(
                        (left_value - left_mean) * (right_value - right_mean)
                        for left_value, right_value in zip(
                            left_values, right_values, strict=True
                        )
                    )
                    denominator = sqrt(
                        sum((value - left_mean) ** 2 for value in left_values)
                        * sum((value - right_mean) ** 2 for value in right_values)
                    )
                    correlation = numerator / denominator if denominator else None
                matrix[left][right] = correlation
        retrieved_at = max(history.retrieved_at for history in histories)
        return QuantitativeDataset(
            analysis="correlation_analysis",
            symbols=tuple(normalized),
            period=histories[0].period,
            interval=histories[0].interval,
            parameters={
                "alignment": "common_price_dates_before_return_calculation",
                "price_fields": {
                    history.symbol: _price_field(history) for history in histories
                }
            },
            summary={"correlations": matrix, "observations": observations},
            series=(),
            provider="; ".join(
                dict.fromkeys(history.provider for history in histories)
            ),
            source_retrieved_at=retrieved_at,
            calculated_at=datetime.now(UTC),
        )

    async def technical_indicators(
        self,
        symbol: str,
        *,
        period: str = "1y",
        interval: str = "1d",
        short_window: int = 20,
        long_window: int = 50,
        rsi_period: int = 14,
    ) -> QuantitativeDataset:
        if (
            isinstance(short_window, bool)
            or isinstance(long_window, bool)
            or not 2 <= short_window < long_window <= 200
        ):
            raise InvalidQuantitativeQueryError(
                "Use integer moving-average windows with 2 <= short < long <= 200."
            )
        if isinstance(rsi_period, bool) or not 2 <= rsi_period <= 50:
            raise InvalidQuantitativeQueryError("Use an RSI period between 2 and 50.")
        history = await self._history(symbol, period=period, interval=interval)
        _require_observations(history, long_window)
        closes = _analysis_prices(history)
        short = _sma(closes, short_window)
        long = _sma(closes, long_window)
        exponential = _ema(closes, short_window)
        relative_strength = _rsi(closes, rsi_period)
        series = [
            {
                "timestamp": point.timestamp,
                "price": closes[index],
                "sma_short": short[index],
                "sma_long": long[index],
                "ema_short": exponential[index],
                "rsi": relative_strength[index],
            }
            for index, point in enumerate(history.points)
        ]
        latest = series[-1]
        return self._dataset(
            history,
            analysis="technical_indicators",
            parameters={
                "short_window": short_window,
                "long_window": long_window,
                "rsi_period": rsi_period,
                "ema_seed": "first_observation",
                "price_field": _price_field(history),
            },
            summary={
                "observations": len(series),
                "latest_price": latest["price"],
                "latest_sma_short": latest["sma_short"],
                "latest_sma_long": latest["sma_long"],
                "latest_ema_short": latest["ema_short"],
                "latest_rsi": latest["rsi"],
                "trend": (
                    "above"
                    if float(latest["sma_short"]) > float(latest["sma_long"])
                    else "below"
                ),
            },
            series=series,
        )

    async def backtest_moving_average(
        self,
        symbol: str,
        *,
        period: str = "5y",
        interval: str = "1d",
        short_window: int = 20,
        long_window: int = 50,
        transaction_cost_bps: float = 10.0,
    ) -> QuantitativeDataset:
        if (
            isinstance(short_window, bool)
            or isinstance(long_window, bool)
            or not 2 <= short_window < long_window <= 200
        ):
            raise InvalidQuantitativeQueryError(
                "Use integer moving-average windows with 2 <= short < long <= 200."
            )
        if (
            isinstance(transaction_cost_bps, bool)
            or not 0 <= transaction_cost_bps <= 1_000
        ):
            raise InvalidQuantitativeQueryError(
                "Choose transaction costs from 0 to 1000 basis points."
            )
        history = await self._history(symbol, period=period, interval=interval)
        _require_observations(history, long_window + 2)
        closes = _analysis_prices(history)
        short = _sma(closes, short_window)
        long = _sma(closes, long_window)
        signals = [
            int(
                short_value is not None
                and long_value is not None
                and short_value > long_value
            )
            for short_value, long_value in zip(short, long, strict=True)
        ]
        cost_rate = transaction_cost_bps / 10_000
        equity = 1.0
        benchmark = 1.0
        prior_position = 0
        trades = 0
        entries = 0
        exits = 0
        exposure_periods = 0
        strategy_returns: list[float] = []
        series: list[dict[str, object]] = [
            {
                "timestamp": history.points[0].timestamp,
                "price": closes[0],
                "position": 0,
                "strategy_equity": equity,
                "benchmark_equity": benchmark,
            }
        ]
        for index in range(1, len(closes)):
            position = signals[index - 1]
            asset_return = closes[index] / closes[index - 1] - 1
            changed = position != prior_position
            if changed:
                trades += 1
            if changed and position == 1:
                entries += 1
            if changed and position == 0:
                exits += 1
            strategy_return = (1 + position * asset_return) * (
                1 - cost_rate if changed else 1
            ) - 1
            equity *= 1 + strategy_return
            benchmark *= 1 + asset_return
            strategy_returns.append(strategy_return)
            exposure_periods += position
            series.append(
                {
                    "timestamp": history.points[index].timestamp,
                    "price": closes[index],
                    "position": position,
                    "strategy_return": strategy_return,
                    "strategy_equity": equity,
                    "benchmark_equity": benchmark,
                }
            )
            prior_position = position
        drawdowns, drawdown_summary = _drawdown_records(
            [point.timestamp for point in history.points],
            [float(record["strategy_equity"]) for record in series],
        )
        for record, drawdown in zip(series, drawdowns, strict=True):
            record["strategy_drawdown"] = drawdown["drawdown"]
        annualization = _PERIODS_PER_YEAR[history.interval]
        total_return = equity - 1
        summary: dict[str, object] = {
            "strategy_total_return": total_return,
            "strategy_annualized_return": _annualized_return(
                total_return, len(strategy_returns), annualization
            ),
            "strategy_annualized_volatility": _sample_volatility(
                strategy_returns, annualization
            ),
            "strategy_maximum_drawdown": drawdown_summary["maximum_drawdown"],
            "benchmark_total_return": benchmark - 1,
            "trades": trades,
            "entries": entries,
            "exits": exits,
            "ending_position": prior_position,
            "exposure_fraction": exposure_periods / len(strategy_returns),
            "final_equity": equity,
        }
        return self._dataset(
            history,
            analysis="moving_average_backtest",
            parameters={
                "short_window": short_window,
                "long_window": long_window,
                "transaction_cost_bps": transaction_cost_bps,
                "transaction_cost_basis": "per_one_way_position_change",
                "signal_lag_periods": 1,
                "positioning": "long_or_cash",
                "price_field": _price_field(history),
            },
            summary=summary,
            series=series,
        )
