"""Narrow provider contract consumed by application services."""

from typing import Protocol

from app.domain.company import CompanyOverview
from app.domain.fundamentals import FundamentalDataset
from app.domain.prices import PriceHistory


class FinancialDataProvider(Protocol):
    """Contract implemented by company-data providers."""

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        """Retrieve a normalized company snapshot.

        Args:
            symbol: Valid, normalized ticker symbol.

        Returns:
            Provider data mapped into ``CompanyOverview``.

        Raises:
            SymbolNotFoundError: If the provider has no data for ``symbol``.
            FinancialProviderError: If the upstream provider cannot complete
                the request.
        """
        ...

    async def get_price_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> PriceHistory:
        """Retrieve a normalized, bounded historical price series."""
        ...

    async def get_financial_statements(
        self, symbol: str, *, frequency: str
    ) -> FundamentalDataset:
        """Retrieve selected income, balance-sheet, and cash-flow rows."""
        ...

    async def get_fundamental_ratios(self, symbol: str) -> FundamentalDataset:
        """Retrieve normalized valuation and operating ratios."""
        ...

    async def get_analyst_estimates(self, symbol: str) -> FundamentalDataset:
        """Retrieve bounded earnings, revenue, and price-target estimates."""
        ...

    async def get_sec_filings(self, symbol: str, *, limit: int) -> FundamentalDataset:
        """Retrieve recent SEC filing metadata and EDGAR links."""
        ...

    async def get_ownership(self, symbol: str, *, limit: int) -> FundamentalDataset:
        """Retrieve institutional and aggregate ownership evidence."""
        ...

    async def get_insider_activity(
        self, symbol: str, *, limit: int
    ) -> FundamentalDataset:
        """Retrieve recent provider-reported insider transactions."""
        ...

    async def get_company_news(self, symbol: str, *, limit: int) -> FundamentalDataset:
        """Retrieve recent company headlines and source links."""
        ...
