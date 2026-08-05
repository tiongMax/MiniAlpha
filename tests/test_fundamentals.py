"""Deterministic Phase 11 fundamental-data and tool tests."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from langchain_core.messages import ToolMessage

from app.agent.tools import (
    create_company_comparison_tool,
    create_company_news_tool,
    create_default_tools,
    create_financial_statements_tool,
)
from app.domain.company import CompanyOverview
from app.domain.errors import InvalidFundamentalQueryError
from app.domain.fundamentals import FundamentalDataset
from app.providers.yahoo import YahooFinanceProvider
from app.services.company_research import CompanyResearchService


class FundamentalTicker:
    """Yahoo-compatible deterministic fundamental response."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def get_info(self):
        return {
            "symbol": self.symbol,
            "currency": "USD",
            "financialCurrency": "USD",
            "trailingPE": 20,
            "forwardPE": 18,
            "grossMargins": 0.4,
            "returnOnEquity": 0.3,
            "currentRatio": 1.5,
            "debtToEquity": 75,
            "heldPercentInsiders": 0.02,
            "heldPercentInstitutions": 0.7,
            "sharesOutstanding": 1000,
        }

    def get_income_stmt(self, **_kwargs):
        period = datetime(2025, 12, 31, tzinfo=UTC)
        return {
            period: {
                "TotalRevenue": 1000,
                "OperatingIncome": 250,
                "NetIncome": 200,
                "DilutedEPS": 2.5,
            }
        }

    def get_balance_sheet(self, **_kwargs):
        period = datetime(2025, 12, 31, tzinfo=UTC)
        return {
            period: {
                "TotalAssets": 5000,
                "TotalDebt": 700,
                "StockholdersEquity": 2500,
            }
        }

    def get_cash_flow(self, **_kwargs):
        period = datetime(2025, 12, 31, tzinfo=UTC)
        return {
            period: {
                "OperatingCashFlow": 300,
                "CapitalExpenditure": -80,
                "FreeCashFlow": 220,
            }
        }

    def get_earnings_estimate(self, **_kwargs):
        return {
            "numberOfAnalysts": {"0q": 12},
            "avg": {"0q": 1.25},
            "growth": {"0q": 0.1},
        }

    def get_revenue_estimate(self, **_kwargs):
        return {
            "numberOfAnalysts": {"0q": 10},
            "avg": {"0q": 25_000},
            "growth": {"0q": 0.08},
        }

    def get_analyst_price_targets(self):
        return {"current": 100, "mean": 120, "low": 90, "high": 140}

    def get_sec_filings(self):
        return [
            {
                "type": "10-K",
                "title": "Annual report",
                "date": date(2026, 2, 1),
                "edgarUrl": "https://www.sec.gov/Archives/example.htm",
            }
        ]

    def get_institutional_holders(self, **_kwargs):
        return {
            "Holder": {0: "Example Asset Management"},
            "Shares": {0: 500},
            "Value": {0: 50_000},
            "Date Reported": {0: datetime(2026, 6, 30, tzinfo=UTC)},
        }

    def get_insider_transactions(self, **_kwargs):
        return {
            "Insider": {0: "A. Director"},
            "Position": {0: "Director"},
            "Transaction": {0: "Sale"},
            "Shares": {0: 100},
            "Start Date": {0: datetime(2026, 7, 1, tzinfo=UTC)},
            "URL": {0: "https://finance.yahoo.com/insider/example"},
        }

    def get_news(self, **_kwargs):
        return [
            {
                "content": {
                    "title": "Example announces results",
                    "summary": "A bounded summary.",
                    "pubDate": "2026-08-01T12:00:00Z",
                    "provider": {"displayName": "Example News"},
                    "canonicalUrl": {"url": "https://example.com/results"},
                }
            }
        ]


@pytest.fixture
def provider(monkeypatch) -> YahooFinanceProvider:
    monkeypatch.setattr(
        "app.providers.yahoo.yf.Ticker", lambda symbol: FundamentalTicker(symbol)
    )
    return YahooFinanceProvider()


def test_maps_bounded_financial_statements(provider) -> None:
    result = provider._fetch_financial_statements("TEST", "yearly")

    assert result.dataset == "financial_statements"
    assert result.currency == "USD"
    assert result.records[0]["period_end"] == "2025-12-31"
    assert result.records[0]["income_statement"]["revenue"] == 1000
    assert result.records[0]["balance_sheet"]["total_debt"] == 700
    assert result.records[0]["cash_flow"]["free_cash_flow"] == 220
    assert result.source_urls[0] == "https://finance.yahoo.com/quote/TEST/financials"


def test_maps_ratios_and_estimates(provider) -> None:
    ratios = provider._fetch_fundamental_ratios("TEST")
    estimates = provider._fetch_analyst_estimates("TEST")

    assert ratios.records[0]["metrics"]["trailing_pe"] == 20.0
    assert ratios.records[1]["metrics"]["return_on_equity"] == 0.3
    current_quarter = next(row for row in estimates.records if row["period"] == "0q")
    assert current_quarter["earnings"]["avg"] == 1.25
    assert current_quarter["revenue"]["avg"] == 25_000


def test_maps_filings_ownership_insiders_and_news(provider) -> None:
    filings = provider._fetch_sec_filings("TEST", 5)
    ownership = provider._fetch_ownership("TEST", 5)
    insiders = provider._fetch_insider_activity("TEST", 5)
    news = provider._fetch_company_news("TEST", 5)

    assert filings.records[0]["form"] == "10-K"
    assert filings.records[0]["filed_at"] == "2026-02-01"
    assert filings.source_urls[0].startswith("https://www.sec.gov/")
    assert ownership.records[0]["institution_percent"] == 0.7
    assert ownership.records[1]["Holder"] == "Example Asset Management"
    assert insiders.records[0]["Insider"] == "A. Director"
    assert news.records[0]["publisher"] == "Example News"
    assert news.records[0]["url"] == "https://example.com/results"


class FundamentalServiceDouble:
    async def get_financial_statements(self, symbol: str, *, frequency: str):
        return FundamentalDataset(
            symbol=symbol,
            dataset="financial_statements",
            currency="USD",
            records=({"period_end": "2025-12-31", "revenue": 1000},),
            provider="Test Provider",
            retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        )

    async def get_company_news(self, symbol: str, *, limit: int):
        return FundamentalDataset(
            symbol=symbol,
            dataset="company_news",
            currency=None,
            records=(
                {
                    "title": "A headline",
                    "publisher": "Test News",
                    "published_at": "2026-08-05T00:00:00Z",
                    "url": "https://example.com/news",
                },
            ),
            provider="Test Provider",
            retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        )


def _invoke(tool, args: dict[str, object]) -> ToolMessage:
    result = asyncio.run(
        tool.ainvoke(
            {"type": "tool_call", "id": "phase-11", "name": tool.name, "args": args}
        )
    )
    assert isinstance(result, ToolMessage)
    return result


def test_fundamental_tools_include_source_metadata() -> None:
    service = FundamentalServiceDouble()
    statements = _invoke(
        create_financial_statements_tool(service),
        {"symbol": "TEST", "frequency": "yearly"},
    )
    news = _invoke(create_company_news_tool(service), {"symbol": "TEST", "limit": 5})

    assert "Source: Test Provider" in statements.content
    assert statements.artifact["data"]["retrieved_at"] == "2026-08-05T00:00:00+00:00"
    assert "A headline" in news.content
    assert news.artifact["artifact_type"] == "company_news"


def test_default_toolset_exposes_complete_phase_eleven_surface() -> None:
    assert {tool.name for tool in create_default_tools()} == {
        "get_company_overview",
        "get_price_history",
        "get_financial_statements",
        "get_fundamental_ratios",
        "get_analyst_estimates",
        "get_sec_filings",
        "get_ownership",
        "get_insider_activity",
        "get_company_news",
        "compare_companies",
    }


class ComparisonProvider:
    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        return CompanyOverview(
            symbol=symbol,
            company_name=symbol,
            exchange="TEST",
            currency="USD",
            sector=None,
            industry=None,
            price=100,
            market_cap=1_000,
            trailing_pe=20,
            forward_pe=18,
            price_to_book=3,
            total_revenue=500,
            revenue_growth=0.1,
            operating_margin=0.2,
            profit_margin=0.15,
            total_cash=100,
            total_debt=50,
            dividend_yield=0.01,
            beta=1,
            provider="Test Provider",
            retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        )

    async def get_financial_statements(self, symbol: str, *, frequency: str):
        self.statement_frequency = frequency
        return FundamentalDataset(
            symbol=symbol,
            dataset="financial_statements",
            currency="USD",
            records=(),
            provider="Test Provider",
            retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        )


def test_comparison_normalizes_deduplicates_and_bounds_symbols() -> None:
    service = CompanyResearchService(ComparisonProvider())
    result = asyncio.run(service.compare_companies([" aapl ", "MSFT", "AAPL"]))

    assert [record["symbol"] for record in result.records] == ["AAPL", "MSFT"]
    tool_result = _invoke(
        create_company_comparison_tool(service), {"symbols": ["AAPL", "MSFT"]}
    )
    assert tool_result.artifact["artifact_type"] == "company_comparison"
    with pytest.raises(InvalidFundamentalQueryError):
        asyncio.run(service.compare_companies(["AAPL"]))
    with pytest.raises(InvalidFundamentalQueryError):
        asyncio.run(service.get_financial_statements("AAPL", frequency="monthly"))
    with pytest.raises(InvalidFundamentalQueryError):
        asyncio.run(service.get_company_news("AAPL", limit=0))


@pytest.mark.parametrize(
    ("supplied", "normalized"),
    [("annual", "yearly"), ("annually", "yearly"), ("quarter", "quarterly")],
)
def test_statement_frequency_aliases_are_normalized(supplied, normalized) -> None:
    provider = ComparisonProvider()
    service = CompanyResearchService(provider)

    result = asyncio.run(service.get_financial_statements("aapl", frequency=supplied))

    assert result.symbol == "AAPL"
    assert provider.statement_frequency == normalized
