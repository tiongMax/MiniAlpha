"""Deterministic financial services shared by live-model experiments."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain_core.tools import BaseTool

from app.agent.tools import create_financial_tools
from app.domain.company import CompanyOverview
from app.domain.fundamentals import FundamentalDataset
from app.domain.prices import PriceHistory, PricePoint
from app.services.company_research import CompanyResearchService
from app.services.quantitative_research import QuantitativeResearchService


class SyntheticFinancialProvider:
    """Return stable local data while exercising production service and tool code."""

    provider_name = "MiniAlpha Synthetic Evaluation Provider"

    @staticmethod
    def _timestamp() -> datetime:
        return datetime(2026, 8, 13, tzinfo=UTC)

    def _dataset(
        self,
        symbol: str,
        dataset: str,
        *,
        limit: int = 1,
    ) -> FundamentalDataset:
        return FundamentalDataset(
            symbol=symbol,
            dataset=dataset,
            currency="USD",
            records=tuple(
                {"record": index + 1, "value": 100 + index}
                for index in range(limit)
            ),
            provider=self.provider_name,
            retrieved_at=self._timestamp(),
            source_urls=("https://example.test/synthetic-financial-data",),
        )

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        return CompanyOverview(
            symbol=symbol,
            company_name=f"{symbol} Corporation",
            exchange="NASDAQ",
            currency="USD",
            sector="Technology",
            industry="Software",
            price=200.0,
            market_cap=1_000_000_000_000.0,
            trailing_pe=25.0,
            forward_pe=22.0,
            price_to_book=10.0,
            total_revenue=100_000_000_000.0,
            revenue_growth=0.1,
            operating_margin=0.25,
            profit_margin=0.2,
            total_cash=50_000_000_000.0,
            total_debt=20_000_000_000.0,
            dividend_yield=0.01,
            beta=1.1,
            provider=self.provider_name,
            retrieved_at=self._timestamp(),
        )

    async def get_price_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> PriceHistory:
        start = self._timestamp() - timedelta(days=299)
        points = tuple(
            PricePoint(
                timestamp=start + timedelta(days=index),
                open=100 + index * 0.1,
                high=101 + index * 0.1,
                low=99 + index * 0.1,
                close=100.5 + index * 0.1,
                adjusted_close=100.5 + index * 0.1,
                volume=1_000_000,
            )
            for index in range(300)
        )
        return PriceHistory(
            symbol=symbol,
            currency="USD",
            period=period,
            interval=interval,
            points=points,
            provider=self.provider_name,
            retrieved_at=self._timestamp(),
        )

    async def get_financial_statements(
        self,
        symbol: str,
        *,
        frequency: str,
    ) -> FundamentalDataset:
        return self._dataset(symbol, f"financial_statements_{frequency}")

    async def get_fundamental_ratios(self, symbol: str) -> FundamentalDataset:
        return self._dataset(symbol, "fundamental_ratios")

    async def get_analyst_estimates(self, symbol: str) -> FundamentalDataset:
        return self._dataset(symbol, "analyst_estimates")

    async def get_sec_filings(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> FundamentalDataset:
        return self._dataset(symbol, "sec_filings", limit=limit)

    async def get_ownership(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> FundamentalDataset:
        return self._dataset(symbol, "ownership", limit=limit)

    async def get_insider_activity(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> FundamentalDataset:
        return self._dataset(symbol, "insider_activity", limit=limit)

    async def get_company_news(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> FundamentalDataset:
        return self._dataset(symbol, "company_news", limit=limit)


def create_synthetic_financial_tools() -> list[BaseTool]:
    """Create the production tool surface backed entirely by stable local data."""
    company = CompanyResearchService(SyntheticFinancialProvider())
    quantitative = QuantitativeResearchService(company)
    return list(create_financial_tools(company, quantitative))
