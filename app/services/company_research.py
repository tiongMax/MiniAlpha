"""Provider-neutral company research orchestration."""

import re

from app.domain.company import CompanyOverview
from app.domain.errors import InvalidPriceQueryError, InvalidSymbolError
from app.domain.prices import PriceHistory
from app.providers.base import FinancialDataProvider

_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=-]{0,19}$")
_PRICE_PERIODS = frozenset({"1mo", "3mo", "6mo", "1y", "2y", "5y"})
_PRICE_INTERVALS = frozenset({"1d", "1wk", "1mo"})


def normalize_symbol(symbol: str) -> str:
    """Normalize and validate a user-supplied ticker.

    Args:
        symbol: Raw ticker text, possibly containing whitespace or lowercase
            letters.

    Returns:
        Trimmed uppercase ticker accepted by the provider boundary.

    Raises:
        InvalidSymbolError: If the ticker is empty, longer than 20 characters,
            or contains unsupported characters.
    """
    normalized = symbol.strip().upper()
    if not normalized or not _SYMBOL_PATTERN.fullmatch(normalized):
        raise InvalidSymbolError(
            "Enter a valid ticker symbol, for example AAPL, BRK-B, or 0700.HK."
        )
    return normalized


class CompanyResearchService:
    """Coordinate company research independently of a concrete provider.

    Attributes:
        provider: Financial-data implementation used for normalized lookups.
    """

    def __init__(self, provider: FinancialDataProvider) -> None:
        """Initialize the service with an injected provider.

        Args:
            provider: Object satisfying ``FinancialDataProvider``.
        """
        self.provider = provider

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        """Validate a ticker and retrieve its normalized company snapshot.

        Args:
            symbol: Raw ticker supplied by a user or model tool call.

        Returns:
            Company overview produced by the configured provider.

        Raises:
            InvalidSymbolError: If ``symbol`` fails local validation.
            SymbolNotFoundError: If the provider has no matching company.
            FinancialProviderError: If the provider request fails.
        """
        return await self.provider.get_company_overview(normalize_symbol(symbol))

    async def get_price_history(
        self,
        symbol: str,
        *,
        period: str = "6mo",
        interval: str = "1d",
    ) -> PriceHistory:
        """Validate and retrieve a chart-sized historical price series."""
        normalized_period = period.strip().lower()
        normalized_interval = interval.strip().lower()
        if normalized_period not in _PRICE_PERIODS:
            raise InvalidPriceQueryError(
                "Choose a price period from 1mo, 3mo, 6mo, 1y, 2y, or 5y."
            )
        if normalized_interval not in _PRICE_INTERVALS:
            raise InvalidPriceQueryError(
                "Choose a price interval from 1d, 1wk, or 1mo."
            )
        return await self.provider.get_price_history(
            normalize_symbol(symbol),
            period=normalized_period,
            interval=normalized_interval,
        )
