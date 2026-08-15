"""Yahoo Finance adapter for the normalized company-data contract."""

import asyncio
import math
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Protocol, SupportsFloat, cast
from urllib.parse import quote

import yfinance as yf

from app.domain.company import CompanyOverview
from app.domain.errors import (
    FinancialProviderError,
    FinancialProviderTimeout,
    SymbolNotFoundError,
)
from app.domain.fundamentals import FundamentalDataset
from app.domain.prices import PriceHistory, PricePoint
from app.observability import observe_span


class _StringKeyed(Protocol):
    """Structural type for yfinance objects supporting string-key access."""

    def __getitem__(self, key: str, /) -> object:
        """Return the provider value stored under ``key``."""
        ...


def _value(source: object, key: str) -> object | None:
    """Read an optional value from a mapping-like provider object.

    Args:
        source: Mapping or lazy object supporting key access.
        key: Provider field to retrieve.

    Returns:
        The field value, or ``None`` when the object does not expose it.
    """
    try:
        if isinstance(source, Mapping):
            return source.get(key)
        keyed_source = cast(_StringKeyed, source)
        return keyed_source[key]
    except (KeyError, TypeError, AttributeError):
        return None


def _first(*values: object | None) -> object | None:
    """Return the first value that is not ``None``.

    Args:
        *values: Candidate values in priority order.

    Returns:
        The first non-``None`` candidate, or ``None`` when all are missing.
        Falsey values such as zero are preserved.
    """
    return next((value for value in values if value is not None), None)


def _number(value: object) -> float | None:
    """Convert a provider value into a float without inventing missing data.

    Args:
        value: Arbitrary value returned by the provider.

    Returns:
        A float when conversion is possible; otherwise ``None``. Boolean
        values are rejected because they are not meaningful financial numbers.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric_value = cast(str | SupportsFloat, value)
        converted = float(numeric_value)
        return converted if math.isfinite(converted) else None
    except (TypeError, ValueError):
        return None


def _percentage_points_to_decimal(value: object) -> float | None:
    """Convert percentage points into the domain's decimal-fraction format.

    Args:
        value: Percentage-point value, where ``2.5`` means 2.5%.

    Returns:
        Decimal fraction such as ``0.025``, or ``None`` for invalid input.
    """
    number = _number(value)
    return None if number is None else number / 100


def _text(value: object) -> str | None:
    """Return provider text without coercing unrelated values.

    Args:
        value: Arbitrary provider field.

    Returns:
        The original string, or ``None`` for missing/non-string values.
    """
    return value if isinstance(value, str) else None


def _safe_value(value: object) -> object:
    """Normalize provider scalars without leaking NaN or pandas objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _safe_value(item())
        except (TypeError, ValueError):
            return None
    numeric = _number(value)
    return numeric if numeric is not None else str(value)


def _rows_from_columns(data: object) -> list[dict[str, object]]:
    """Convert DataFrame ``to_dict`` output into stable row dictionaries."""
    if not isinstance(data, Mapping):
        return []
    rows: dict[str, dict[str, object]] = {}
    for raw_column, raw_values in data.items():
        if not isinstance(raw_values, Mapping):
            continue
        for raw_index, value in raw_values.items():
            index = (
                raw_index.isoformat()
                if isinstance(raw_index, (datetime, date))
                else str(raw_index)
            )
            rows.setdefault(index, {"period": index})[str(raw_column)] = _safe_value(
                value
            )
    return list(rows.values())


def _selected(source: Mapping[object, object], fields: Mapping[str, tuple[str, ...]]):
    """Select the first available Yahoo alias for each normalized metric."""
    result: dict[str, object] = {}
    for target, aliases in fields.items():
        raw = next((source[key] for key in aliases if key in source), None)
        result[target] = _safe_value(raw)
    return result


def _quote_url(symbol: str, page: str = "") -> str:
    """Build a source URL for a Yahoo Finance ticker page."""
    suffix = f"/{page}" if page else ""
    return f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}{suffix}"


class YahooFinanceProvider:
    """Retrieve and normalize company data from Yahoo Finance.

    The yfinance client is synchronous, so public calls run it in a worker
    thread and enforce an asynchronous timeout.

    Attributes:
        timeout_seconds: Maximum time to await one company lookup.
    """

    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        """Initialize the Yahoo adapter.

        Args:
            timeout_seconds: Number of seconds allowed for an upstream lookup
                before raising ``FinancialProviderTimeout``.
        """
        self.timeout_seconds = timeout_seconds

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        """Retrieve a Yahoo company snapshot without blocking the event loop.

        Args:
            symbol: Valid, normalized Yahoo-compatible ticker symbol.

        Returns:
            Yahoo fields normalized into ``CompanyOverview``.

        Raises:
            FinancialProviderTimeout: If the lookup exceeds
                ``timeout_seconds``.
            SymbolNotFoundError: If Yahoo returns no identifying, price, or
                market-cap data.
            FinancialProviderError: If any other upstream failure occurs.
        """
        with observe_span(
            "provider.request",
            run_type="tool",
            metadata={
                "provider": "yahoo_finance",
                "provider_operation": "overview",
                "attempt": 1,
            },
        ) as span:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._fetch, symbol),
                    timeout=self.timeout_seconds,
                )
                span.set_attribute("outcome", "ok")
                return result
            except TimeoutError as error:
                raise FinancialProviderTimeout(
                    f"Yahoo Finance timed out while looking up {symbol}."
                ) from error
            except SymbolNotFoundError:
                raise
            except Exception as error:
                raise FinancialProviderError(
                    f"Yahoo Finance could not retrieve data for {symbol}."
                ) from error

    async def get_price_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> PriceHistory:
        """Retrieve normalized Yahoo OHLCV history off the event loop."""
        with observe_span(
            "provider.request",
            run_type="tool",
            metadata={
                "provider": "yahoo_finance",
                "provider_operation": "price_history",
                "attempt": 1,
            },
        ) as span:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._fetch_price_history,
                        symbol,
                        period,
                        interval,
                    ),
                    timeout=self.timeout_seconds,
                )
                span.set_attributes(
                    {"outcome": "ok", "record_count": len(result.points)}
                )
                return result
            except TimeoutError as error:
                raise FinancialProviderTimeout(
                    f"Yahoo Finance timed out while retrieving prices for {symbol}."
                ) from error
            except SymbolNotFoundError:
                raise
            except Exception as error:
                raise FinancialProviderError(
                    f"Yahoo Finance could not retrieve prices for {symbol}."
                ) from error

    async def _get_dataset(
        self,
        symbol: str,
        description: str,
        fetch: Callable[..., FundamentalDataset],
        *args: object,
    ) -> FundamentalDataset:
        """Run one blocking fundamental lookup with consistent translation."""
        operation = description.casefold().replace(" ", "_")
        with observe_span(
            "provider.request",
            run_type="tool",
            metadata={
                "provider": "yahoo_finance",
                "provider_operation": operation,
                "attempt": 1,
            },
        ) as span:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(fetch, symbol, *args),
                    timeout=self.timeout_seconds,
                )
                span.set_attributes(
                    {"outcome": "ok", "record_count": len(result.records)}
                )
                return result
            except TimeoutError as error:
                timeout_message = (
                    "Yahoo Finance timed out while retrieving "
                    f"{description} for {symbol}."
                )
                raise FinancialProviderTimeout(timeout_message) from error
            except SymbolNotFoundError:
                raise
            except Exception as error:
                raise FinancialProviderError(
                    f"Yahoo Finance could not retrieve {description} for {symbol}."
                ) from error

    async def get_financial_statements(
        self, symbol: str, *, frequency: str
    ) -> FundamentalDataset:
        return await self._get_dataset(
            symbol, "financial statements", self._fetch_financial_statements, frequency
        )

    async def get_fundamental_ratios(self, symbol: str) -> FundamentalDataset:
        return await self._get_dataset(
            symbol, "fundamental ratios", self._fetch_fundamental_ratios
        )

    async def get_analyst_estimates(self, symbol: str) -> FundamentalDataset:
        return await self._get_dataset(
            symbol, "analyst estimates", self._fetch_analyst_estimates
        )

    async def get_sec_filings(self, symbol: str, *, limit: int) -> FundamentalDataset:
        return await self._get_dataset(
            symbol, "SEC filings", self._fetch_sec_filings, limit
        )

    async def get_ownership(self, symbol: str, *, limit: int) -> FundamentalDataset:
        return await self._get_dataset(
            symbol, "ownership", self._fetch_ownership, limit
        )

    async def get_insider_activity(
        self, symbol: str, *, limit: int
    ) -> FundamentalDataset:
        return await self._get_dataset(
            symbol, "insider activity", self._fetch_insider_activity, limit
        )

    async def get_company_news(self, symbol: str, *, limit: int) -> FundamentalDataset:
        return await self._get_dataset(
            symbol, "company news", self._fetch_company_news, limit
        )

    def _fetch(self, symbol: str) -> CompanyOverview:
        """Perform the blocking Yahoo lookup and field mapping.

        Args:
            symbol: Valid, normalized Yahoo-compatible ticker symbol.

        Returns:
            A normalized company overview stamped with the retrieval time.

        Raises:
            SymbolNotFoundError: If the response lacks enough data to identify
                a company.
            Exception: A yfinance or network error, which the public async
                method translates into ``FinancialProviderError``.
        """
        ticker = yf.Ticker(symbol)
        info: Mapping[str, object] = ticker.get_info() or {}
        fast_info = ticker.fast_info

        price = _number(
            _first(
                _value(fast_info, "last_price"),
                info.get("currentPrice"),
                info.get("regularMarketPrice"),
            )
        )
        market_cap = _number(
            _first(_value(fast_info, "market_cap"), info.get("marketCap"))
        )
        company_name = _text(_first(info.get("longName"), info.get("shortName")))

        if company_name is None and price is None and market_cap is None:
            raise SymbolNotFoundError(
                f"Yahoo Finance has no company data for {symbol}."
            )

        return CompanyOverview(
            symbol=str(info.get("symbol") or symbol).upper(),
            company_name=company_name,
            exchange=_text(_first(info.get("fullExchangeName"), info.get("exchange"))),
            currency=_text(_first(info.get("currency"), _value(fast_info, "currency"))),
            sector=_text(info.get("sector")),
            industry=_text(info.get("industry")),
            price=price,
            market_cap=market_cap,
            trailing_pe=_number(info.get("trailingPE")),
            forward_pe=_number(info.get("forwardPE")),
            price_to_book=_number(info.get("priceToBook")),
            total_revenue=_number(info.get("totalRevenue")),
            revenue_growth=_number(info.get("revenueGrowth")),
            operating_margin=_number(info.get("operatingMargins")),
            profit_margin=_number(info.get("profitMargins")),
            total_cash=_number(info.get("totalCash")),
            total_debt=_number(info.get("totalDebt")),
            dividend_yield=_number(
                _first(
                    info.get("trailingAnnualDividendYield"),
                    _percentage_points_to_decimal(info.get("dividendYield")),
                )
            ),
            beta=_number(info.get("beta")),
            provider="Yahoo Finance",
            retrieved_at=datetime.now(UTC),
        )

    def _fetch_price_history(
        self,
        symbol: str,
        period: str,
        interval: str,
    ) -> PriceHistory:
        """Perform the blocking Yahoo history request and normalize rows."""
        ticker = yf.Ticker(symbol)
        history = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
        )
        points: list[PricePoint] = []
        for raw_timestamp, row in history.iterrows():
            close = _number(_value(row, "Close"))
            if close is None:
                continue
            timestamp = raw_timestamp.to_pydatetime()
            # Yahoo daily/weekly/monthly indices are session labels rather than
            # executable instants. Preserve that provider date across exchange
            # time zones so cross-market histories can be aligned correctly.
            timestamp = datetime(
                timestamp.year,
                timestamp.month,
                timestamp.day,
                tzinfo=UTC,
            )
            raw_volume = _number(_value(row, "Volume"))
            points.append(
                PricePoint(
                    timestamp=timestamp,
                    open=_number(_value(row, "Open")),
                    high=_number(_value(row, "High")),
                    low=_number(_value(row, "Low")),
                    close=close,
                    volume=int(raw_volume) if raw_volume is not None else None,
                    adjusted_close=_number(_value(row, "Adj Close")),
                )
            )
        if not points:
            raise SymbolNotFoundError(
                f"Yahoo Finance has no price history for {symbol}."
            )
        currency = _text(_value(ticker.fast_info, "currency"))
        return PriceHistory(
            symbol=symbol,
            currency=currency,
            period=period,
            interval=interval,
            points=tuple(points),
            provider="Yahoo Finance",
            retrieved_at=datetime.now(UTC),
        )

    def _fetch_financial_statements(
        self, symbol: str, frequency: str
    ) -> FundamentalDataset:
        ticker = yf.Ticker(symbol)
        income = ticker.get_income_stmt(as_dict=True, freq=frequency) or {}
        balance = ticker.get_balance_sheet(as_dict=True, freq=frequency) or {}
        cash = ticker.get_cash_flow(as_dict=True, freq=frequency) or {}
        periods = sorted(
            set(income) | set(balance) | set(cash),
            key=str,
            reverse=True,
        )[:4]
        income_fields = {
            "revenue": ("TotalRevenue", "OperatingRevenue"),
            "gross_profit": ("GrossProfit",),
            "operating_income": ("OperatingIncome",),
            "ebitda": ("EBITDA", "NormalizedEBITDA"),
            "net_income": ("NetIncome", "NetIncomeCommonStockholders"),
            "diluted_eps": ("DilutedEPS",),
        }
        balance_fields = {
            "cash_and_equivalents": (
                "CashCashEquivalentsAndShortTermInvestments",
                "CashAndCashEquivalents",
            ),
            "current_assets": ("CurrentAssets", "TotalCurrentAssets"),
            "total_assets": ("TotalAssets",),
            "current_liabilities": (
                "CurrentLiabilities",
                "TotalCurrentLiabilities",
            ),
            "total_debt": ("TotalDebt",),
            "stockholders_equity": (
                "StockholdersEquity",
                "TotalStockholderEquity",
            ),
        }
        cash_fields = {
            "operating_cash_flow": ("OperatingCashFlow",),
            "capital_expenditure": ("CapitalExpenditure",),
            "free_cash_flow": ("FreeCashFlow",),
            "dividends_paid": ("CashDividendsPaid", "CommonStockDividendPaid"),
            "share_repurchases": ("RepurchaseOfCapitalStock",),
        }
        records = tuple(
            {
                "period_end": (
                    period.date().isoformat()
                    if isinstance(period, datetime)
                    else period.isoformat()
                    if isinstance(period, date)
                    else str(period)
                ),
                "income_statement": _selected(income.get(period, {}), income_fields),
                "balance_sheet": _selected(balance.get(period, {}), balance_fields),
                "cash_flow": _selected(cash.get(period, {}), cash_fields),
            }
            for period in periods
        )
        if not records:
            raise SymbolNotFoundError(
                f"Yahoo Finance has no {frequency} financial statements for {symbol}."
            )
        info = ticker.get_info() or {}
        return FundamentalDataset(
            symbol=symbol,
            dataset="financial_statements",
            currency=_text(info.get("financialCurrency") or info.get("currency")),
            records=records,
            provider="Yahoo Finance",
            retrieved_at=datetime.now(UTC),
            source_urls=(
                _quote_url(symbol, "financials"),
                _quote_url(symbol, "balance-sheet"),
                _quote_url(symbol, "cash-flow"),
            ),
        )

    def _fetch_fundamental_ratios(self, symbol: str) -> FundamentalDataset:
        info: Mapping[str, object] = yf.Ticker(symbol).get_info() or {}
        groups = (
            (
                "valuation",
                {
                    "trailing_pe": "trailingPE",
                    "forward_pe": "forwardPE",
                    "price_to_sales": "priceToSalesTrailing12Months",
                    "price_to_book": "priceToBook",
                    "enterprise_to_revenue": "enterpriseToRevenue",
                    "enterprise_to_ebitda": "enterpriseToEbitda",
                    "peg_ratio": "pegRatio",
                },
            ),
            (
                "profitability_and_returns",
                {
                    "gross_margin": "grossMargins",
                    "operating_margin": "operatingMargins",
                    "profit_margin": "profitMargins",
                    "return_on_assets": "returnOnAssets",
                    "return_on_equity": "returnOnEquity",
                },
            ),
            (
                "liquidity_and_leverage",
                {
                    "current_ratio": "currentRatio",
                    "quick_ratio": "quickRatio",
                    "debt_to_equity": "debtToEquity",
                },
            ),
            (
                "growth_and_yield",
                {
                    "revenue_growth": "revenueGrowth",
                    "earnings_growth": "earningsGrowth",
                    "dividend_yield": "dividendYield",
                    "payout_ratio": "payoutRatio",
                },
            ),
        )
        records = tuple(
            {
                "category": category,
                "metrics": {
                    name: _number(info.get(key)) for name, key in fields.items()
                },
            }
            for category, fields in groups
        )
        leverage_metrics = cast(dict[str, object], records[2]["metrics"])
        raw_debt_to_equity = leverage_metrics["debt_to_equity"]
        if isinstance(raw_debt_to_equity, float):
            leverage_metrics["debt_to_equity"] = raw_debt_to_equity / 100
        if not any(
            value is not None
            for record in records
            for value in cast(dict[str, object], record["metrics"]).values()
        ):
            raise SymbolNotFoundError(
                f"Yahoo Finance has no fundamental ratios for {symbol}."
            )
        return FundamentalDataset(
            symbol=symbol,
            dataset="fundamental_ratios",
            currency=_text(info.get("financialCurrency") or info.get("currency")),
            records=records,
            provider="Yahoo Finance",
            retrieved_at=datetime.now(UTC),
            source_urls=(_quote_url(symbol, "key-statistics"),),
        )

    def _fetch_analyst_estimates(self, symbol: str) -> FundamentalDataset:
        ticker = yf.Ticker(symbol)
        earnings = _rows_from_columns(ticker.get_earnings_estimate(as_dict=True))
        revenues = _rows_from_columns(ticker.get_revenue_estimate(as_dict=True))
        by_period: dict[str, dict[str, object]] = {}
        for row in earnings:
            period = str(row.pop("period"))
            by_period.setdefault(period, {"period": period})["earnings"] = row
        for row in revenues:
            period = str(row.pop("period"))
            by_period.setdefault(period, {"period": period})["revenue"] = row
        targets = ticker.get_analyst_price_targets() or {}
        if targets:
            by_period["price_targets"] = {
                "period": "price_targets",
                "targets": {
                    str(key): _safe_value(value) for key, value in targets.items()
                },
            }
        records = tuple(by_period.values())
        if not records:
            raise SymbolNotFoundError(
                f"Yahoo Finance has no analyst estimates for {symbol}."
            )
        info = ticker.get_info() or {}
        return FundamentalDataset(
            symbol=symbol,
            dataset="analyst_estimates",
            currency=_text(info.get("financialCurrency") or info.get("currency")),
            records=records,
            provider="Yahoo Finance",
            retrieved_at=datetime.now(UTC),
            source_urls=(_quote_url(symbol, "analysis"),),
        )

    def _fetch_sec_filings(self, symbol: str, limit: int) -> FundamentalDataset:
        raw = yf.Ticker(symbol).get_sec_filings() or []
        if isinstance(raw, Mapping):
            raw = raw.get("filings", [])
        records: list[dict[str, object]] = []
        urls: list[str] = []
        for filing in raw[:limit]:
            if not isinstance(filing, Mapping):
                continue
            url = _text(
                _first(
                    filing.get("edgarUrl"),
                    filing.get("url"),
                    filing.get("link"),
                )
            )
            record = {
                "form": _text(filing.get("type") or filing.get("form")),
                "title": _text(filing.get("title")),
                "filed_at": _safe_value(filing.get("date")),
                "url": url,
            }
            records.append(record)
            if url:
                urls.append(url)
        if not records:
            raise SymbolNotFoundError(f"No SEC filings are available for {symbol}.")
        return FundamentalDataset(
            symbol=symbol,
            dataset="sec_filings",
            currency=None,
            records=tuple(records),
            provider="SEC EDGAR metadata via Yahoo Finance",
            retrieved_at=datetime.now(UTC),
            source_urls=tuple(dict.fromkeys(urls)),
        )

    def _fetch_ownership(self, symbol: str, limit: int) -> FundamentalDataset:
        ticker = yf.Ticker(symbol)
        info = ticker.get_info() or {}
        records = [
            {
                "record_type": "summary",
                "insider_percent": _number(info.get("heldPercentInsiders")),
                "institution_percent": _number(info.get("heldPercentInstitutions")),
                "float_shares": _number(info.get("floatShares")),
                "shares_outstanding": _number(info.get("sharesOutstanding")),
            }
        ]
        for row in _rows_from_columns(ticker.get_institutional_holders(as_dict=True))[
            :limit
        ]:
            row["record_type"] = "institutional_holder"
            records.append(row)
        if len(records) == 1 and not any(
            value is not None
            for key, value in records[0].items()
            if key != "record_type"
        ):
            raise SymbolNotFoundError(f"No ownership data is available for {symbol}.")
        return FundamentalDataset(
            symbol=symbol,
            dataset="ownership",
            currency=_text(info.get("currency")),
            records=tuple(records),
            provider="Yahoo Finance",
            retrieved_at=datetime.now(UTC),
            source_urls=(_quote_url(symbol, "holders"),),
        )

    def _fetch_insider_activity(self, symbol: str, limit: int) -> FundamentalDataset:
        ticker = yf.Ticker(symbol)
        records = _rows_from_columns(ticker.get_insider_transactions(as_dict=True))[
            :limit
        ]
        if not records:
            raise SymbolNotFoundError(f"No insider activity is available for {symbol}.")
        urls = (
            _quote_url(symbol, "insider-transactions"),
            *(str(row["URL"]) for row in records if isinstance(row.get("URL"), str)),
        )
        return FundamentalDataset(
            symbol=symbol,
            dataset="insider_activity",
            currency=None,
            records=tuple(records),
            provider="Yahoo Finance",
            retrieved_at=datetime.now(UTC),
            source_urls=urls,
        )

    def _fetch_company_news(self, symbol: str, limit: int) -> FundamentalDataset:
        articles = yf.Ticker(symbol).get_news(count=limit, tab="news") or []
        records: list[dict[str, object]] = []
        urls: list[str] = []
        for article in articles[:limit]:
            if not isinstance(article, Mapping):
                continue
            content = article.get("content", article)
            if not isinstance(content, Mapping):
                continue
            provider = content.get("provider")
            canonical = content.get("canonicalUrl")
            clickthrough = content.get("clickThroughUrl")
            url = _text(content.get("link"))
            if isinstance(canonical, Mapping):
                url = _text(canonical.get("url")) or url
            if isinstance(clickthrough, Mapping):
                url = _text(clickthrough.get("url")) or url
            publisher = None
            if isinstance(provider, Mapping):
                publisher = _text(provider.get("displayName"))
            publisher = publisher or _text(content.get("publisher"))
            summary = _text(content.get("summary") or content.get("description"))
            if summary and len(summary) > 500:
                summary = f"{summary[:497]}..."
            published = _first(
                content.get("pubDate"),
                content.get("displayTime"),
                content.get("providerPublishTime"),
            )
            if isinstance(published, (int, float)):
                published = datetime.fromtimestamp(published, UTC).isoformat()
            records.append(
                {
                    "title": _text(content.get("title")),
                    "publisher": publisher,
                    "published_at": _safe_value(published),
                    "summary": summary,
                    "url": url,
                }
            )
            if url:
                urls.append(url)
        if not records:
            raise SymbolNotFoundError(f"No recent news is available for {symbol}.")
        return FundamentalDataset(
            symbol=symbol,
            dataset="company_news",
            currency=None,
            records=tuple(records),
            provider="Yahoo Finance and linked publishers",
            retrieved_at=datetime.now(UTC),
            source_urls=tuple(dict.fromkeys(urls)),
        )
