"""Yahoo Finance adapter for the normalized company-data contract."""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Protocol, SupportsFloat, cast

import yfinance as yf

from app.domain.company import CompanyOverview
from app.domain.errors import (
    FinancialProviderError,
    FinancialProviderTimeout,
    SymbolNotFoundError,
)


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
        return float(numeric_value)
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
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._fetch, symbol),
                timeout=self.timeout_seconds,
            )
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
        company_name = _text(
            _first(info.get("longName"), info.get("shortName"))
        )

        if company_name is None and price is None and market_cap is None:
            raise SymbolNotFoundError(
                f"Yahoo Finance has no company data for {symbol}."
            )

        return CompanyOverview(
            symbol=str(info.get("symbol") or symbol).upper(),
            company_name=company_name,
            exchange=_text(
                _first(info.get("fullExchangeName"), info.get("exchange"))
            ),
            currency=_text(
                _first(info.get("currency"), _value(fast_info, "currency"))
            ),
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
            retrieved_at=datetime.now(timezone.utc),
        )
