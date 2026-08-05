# Phase 11 fundamental research

Phase 11 adds bounded, provider-neutral fundamental datasets without changing
the explicit LangGraph model → tools → model loop.

## Tool surface

- `get_financial_statements(symbol, frequency)` returns up to four yearly or
  quarterly periods of selected income-statement, balance-sheet, and cash-flow
  metrics. `annual`, `annually`, and `quarter` normalize to the canonical
  provider values.
- `get_fundamental_ratios(symbol)` groups valuation, profitability, return,
  liquidity, leverage, growth, and yield metrics.
- `get_analyst_estimates(symbol)` returns bounded EPS and revenue consensus
  horizons plus provider price targets. Estimates are not reported results.
- `get_sec_filings(symbol, limit)` returns filing form, title, date, and the
  EDGAR document URL supplied by Yahoo Finance. Filing bodies are not inserted
  into model context.
- `get_ownership(symbol, limit)` returns aggregate insider/institutional
  percentages and leading reported institutional holders.
- `get_insider_activity(symbol, limit)` returns recent provider-reported
  transactions and source links.
- `get_company_news(symbol, limit)` returns recent titles, publishers,
  publication times, bounded summaries, and canonical article links.
- `compare_companies(symbols)` compares normalized overview metrics for two to
  five distinct tickers.

The earlier `get_company_overview` and `get_price_history` tools remain
available. The complete production graph now binds ten explicit tools.

## Data contracts

Every new successful artifact uses schema version 1 and contains:

```text
symbol
dataset
currency
records[]
provider
retrieved_at
source_urls[]
```

The model receives compact bounded text. Structured records are stored in the
artifact envelope and PostgreSQL, then delivered through Redis/SSE. Missing
provider values remain null rather than becoming zero. Expected validation,
symbol, and provider failures become controlled error artifacts.

Controlled failures are stored with `status: error`, so tool cards remain
accurate after transcript reload. If a later corrected call of the same
artifact type succeeds, the superseded error artifact is omitted while both
tool attempts remain visible.

Yahoo Finance is the initial provider. SEC filing metadata is relayed through
Yahoo Finance while retaining the source document link. Provider data can be
delayed, incomplete, revised, or unavailable; news and analyst estimates must
not be presented as audited company results.
