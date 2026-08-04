"""Narrow provider contract consumed by application services."""

from typing import Protocol

from app.domain.company import CompanyOverview


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
