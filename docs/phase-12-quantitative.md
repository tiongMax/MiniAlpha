# Phase 12 deterministic quantitative research

Phase 12 adds deterministic application tools over normalized historical
prices. The language model selects tools and explains their outputs; it does
not calculate statistics, indicators, correlations, or backtest returns.

## Tools

- `calculate_return_statistics(symbol, period, interval)` computes total and
  annualized returns, arithmetic mean return, best/worst period, and the
  positive-period fraction.
- `calculate_volatility(symbol, period, interval)` computes sample period
  volatility, annualized volatility, and annualized downside deviation.
- `analyze_drawdowns(symbol, period, interval)` computes the full drawdown
  series, maximum drawdown peak/trough/recovery, current drawdown, and ulcer
  index.
- `calculate_correlations(symbols, period, interval)` aligns return
  observations by timestamp and computes a pairwise matrix for 2–5 symbols.
- `calculate_technical_indicators(...)` computes short/long simple moving
  averages, a short exponential moving average, and Wilder-style RSI.
- `backtest_moving_average(...)` simulates a bounded long-or-cash moving-average
  crossover with transaction costs.

All tools accept the existing bounded periods (`1mo`, `3mo`, `6mo`, `1y`,
`2y`, `5y`) and intervals (`1d`, `1wk`, `1mo`). Moving-average windows must
satisfy `2 <= short < long <= 200`; RSI is bounded to 2–50 periods; backtest
costs are bounded to 0–1000 basis points.

## Calculation rules

- Adjusted closes are preferred when every observation provides one, which
  avoids treating splits and distributions as ordinary returns. Otherwise the
  calculation explicitly reports that raw close was used.
- Annualization uses 252 daily, 52 weekly, or 12 monthly periods.
- Volatility uses sample standard deviation.
- Correlations are Pearson correlations over pairwise timestamp intersections;
  zero-variance or insufficient pairs return `null`, not a fabricated value.
- RSI uses Wilder smoothing after an initial arithmetic average.
- Backtest signals are shifted one full period before application, preventing
  look-ahead. Transaction costs are applied multiplicatively whenever the
  long/cash position changes.
- The backtest does not model taxes, slippage beyond the configured cost,
  borrow, fractional-share constraints, or execution liquidity. It is
  hypothetical historical analysis, not a forecast.

## Artifacts

Each tool returns a versioned artifact whose type matches its analysis:

```text
return_statistics
volatility_analysis
drawdown_analysis
correlation_analysis
technical_indicators
moving_average_backtest
```

Artifacts contain calculation parameters, compact summary metrics, source and
calculation timestamps, and any bounded series needed for inspection. Only the
summary enters model context. The React frontend renders summary metrics and
correlation matrices instead of raw JSON.
