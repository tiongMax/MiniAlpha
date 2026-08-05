"""Agent-facing financial research tools."""

import json
from collections.abc import Sequence

from langchain_core.tools import BaseTool, tool

from app.domain.company import CompanyOverview
from app.domain.errors import FinancialDataError
from app.domain.fundamentals import FundamentalDataset
from app.domain.prices import PriceHistory
from app.providers.yahoo import YahooFinanceProvider
from app.services.company_research import CompanyResearchService


def _format_money(value: float | None, currency: str | None) -> str:
    """Format a raw monetary value for compact model-readable output.

    Args:
        value: Monetary amount in base currency units, or ``None``.
        currency: Currency code displayed before the amount when available.

    Returns:
        A value abbreviated with M, B, or T where appropriate, or ``"N/A"``
        when the source value is missing.
    """
    if value is None:
        return "N/A"

    absolute = abs(value)
    if absolute >= 1_000_000_000_000:
        amount = f"{value / 1_000_000_000_000:.2f}T"
    elif absolute >= 1_000_000_000:
        amount = f"{value / 1_000_000_000:.2f}B"
    elif absolute >= 1_000_000:
        amount = f"{value / 1_000_000:.2f}M"
    else:
        amount = f"{value:,.2f}"

    return f"{currency} {amount}" if currency else amount


def _format_ratio(value: float | None) -> str:
    """Format a unitless financial ratio.

    Args:
        value: Ratio value, or ``None`` when unavailable.

    Returns:
        Ratio with two decimal places, or ``"N/A"``.
    """
    return "N/A" if value is None else f"{value:.2f}"


def _format_percentage(value: float | None) -> str:
    """Format a decimal fraction as a one-decimal percentage.

    Args:
        value: Decimal fraction such as ``0.125``, or ``None``.

    Returns:
        Percentage such as ``"12.5%"``, or ``"N/A"``.
    """
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _format_yield(value: float | None) -> str:
    """Format a decimal yield with precision suitable for small dividends.

    Args:
        value: Decimal dividend yield such as ``0.0035``, or ``None``.

    Returns:
        Percentage with two decimal places, or ``"N/A"``.
    """
    return "N/A" if value is None else f"{value * 100:.2f}%"


def format_company_overview(overview: CompanyOverview) -> str:
    """Render normalized company data for the model's tool context.

    Args:
        overview: Provider-neutral company snapshot.

    Returns:
        Multiline text containing identity, valuation, growth, profitability,
        balance-sheet, source, and retrieval-time fields. Missing values are
        rendered as ``N/A``.
    """
    identity = overview.company_name or "Unknown company name"
    return "\n".join(
        (
            f"{identity} ({overview.symbol})",
            f"Exchange: {overview.exchange or 'N/A'}",
            f"Sector / industry: {overview.sector or 'N/A'} / "
            f"{overview.industry or 'N/A'}",
            f"Price: {_format_money(overview.price, overview.currency)}",
            f"Market capitalization: "
            f"{_format_money(overview.market_cap, overview.currency)}",
            f"Trailing / forward P/E: {_format_ratio(overview.trailing_pe)} / "
            f"{_format_ratio(overview.forward_pe)}",
            f"Price-to-book: {_format_ratio(overview.price_to_book)}",
            f"Revenue: {_format_money(overview.total_revenue, overview.currency)}",
            f"Revenue growth: {_format_percentage(overview.revenue_growth)}",
            f"Operating margin: {_format_percentage(overview.operating_margin)}",
            f"Profit margin: {_format_percentage(overview.profit_margin)}",
            f"Cash / debt: {_format_money(overview.total_cash, overview.currency)} / "
            f"{_format_money(overview.total_debt, overview.currency)}",
            f"Dividend yield: {_format_yield(overview.dividend_yield)}",
            f"Beta: {_format_ratio(overview.beta)}",
            f"Source: {overview.provider}",
            f"Retrieved: {overview.retrieved_at.isoformat()}",
            "Note: provider data may be delayed or incomplete.",
        )
    )


def format_price_history(history: PriceHistory) -> str:
    """Render a price series compactly without placing every point in context."""
    first = history.points[0]
    last = history.points[-1]
    change = (last.close / first.close - 1) if first.close else None
    closes = [point.close for point in history.points]
    return "\n".join(
        (
            f"{history.symbol} price history ({history.period}, {history.interval})",
            f"Observations: {len(history.points)}",
            f"Start / latest close: "
            f"{_format_money(first.close, history.currency)} / "
            f"{_format_money(last.close, history.currency)}",
            f"Period change: {_format_percentage(change)}",
            f"Low / high close: {_format_money(min(closes), history.currency)} / "
            f"{_format_money(max(closes), history.currency)}",
            f"Source: {history.provider}",
            f"Retrieved: {history.retrieved_at.isoformat()}",
            "Note: provider data may be delayed or incomplete.",
        )
    )


def format_fundamental_dataset(dataset: FundamentalDataset) -> str:
    """Render bounded fundamental evidence compactly for model context."""
    heading = dataset.dataset.replace("_", " ").title()
    lines = [
        f"{dataset.symbol} — {heading}",
        f"Records: {len(dataset.records)}",
    ]
    for record in dataset.records:
        if dataset.dataset == "company_news":
            lines.append(
                f"- {record.get('published_at') or 'Undated'} | "
                f"{record.get('publisher') or 'Unknown publisher'} | "
                f"{record.get('title') or 'Untitled'} | {record.get('url') or 'No URL'}"
            )
        elif dataset.dataset == "sec_filings":
            lines.append(
                f"- {record.get('filed_at') or 'Undated'} | "
                f"{record.get('form') or 'Unknown form'} | "
                f"{record.get('title') or 'Untitled'} | {record.get('url') or 'No URL'}"
            )
        else:
            lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    lines.extend(
        (
            f"Source: {dataset.provider}",
            f"Retrieved: {dataset.retrieved_at.isoformat()}",
            "Note: provider data may be delayed, revised, or incomplete.",
        )
    )
    return "\n".join(lines)


def _dataset_artifact(dataset: FundamentalDataset) -> dict[str, object]:
    return {
        "artifact_type": dataset.dataset,
        "schema_version": 1,
        "status": "ok",
        "data": dataset.to_dict(),
    }


def _error_artifact(
    artifact_type: str, error: FinancialDataError
) -> tuple[str, dict[str, object]]:
    return (
        str(error),
        {
            "artifact_type": artifact_type,
            "schema_version": 1,
            "status": "error",
            "error": str(error),
        },
    )


def create_company_overview_tool(
    service: CompanyResearchService,
) -> BaseTool:
    """Create a LangChain-compatible company research tool.

    Args:
        service: Injected company research service used by tool calls.

    Returns:
        Async tool named ``get_company_overview``. Successful calls return
        formatted content plus a versioned ``company_overview`` artifact.
        Expected financial-data errors are returned as error artifacts so the
        graph can continue to a model response.
    """

    @tool(response_format="content_and_artifact")
    async def get_company_overview(
        symbol: str,
    ) -> tuple[str, dict[str, object]]:
        """Get a company profile and high-level financial overview.

        Args:
            symbol: Public-company ticker, such as AAPL, MSFT, or BRK-B.

        Returns:
            Model-readable financial text and a structured artifact containing
            normalized raw values, source metadata, and schema version.
        """
        try:
            overview = await service.get_company_overview(symbol)
        except FinancialDataError as error:
            return (
                str(error),
                {
                    "artifact_type": "company_overview",
                    "schema_version": 1,
                    "status": "error",
                    "error": str(error),
                },
            )

        return (
            format_company_overview(overview),
            {
                "artifact_type": "company_overview",
                "schema_version": 1,
                "status": "ok",
                "data": overview.to_dict(),
            },
        )

    return get_company_overview


def create_price_history_tool(service: CompanyResearchService) -> BaseTool:
    """Create the chart-producing historical price tool."""

    @tool(response_format="content_and_artifact")
    async def get_price_history(
        symbol: str,
        period: str = "6mo",
        interval: str = "1d",
    ) -> tuple[str, dict[str, object]]:
        """Get historical OHLCV prices for a public-company ticker.

        Args:
            symbol: Public ticker such as AAPL or MSFT.
            period: One of 1mo, 3mo, 6mo, 1y, 2y, or 5y.
            interval: One of 1d, 1wk, or 1mo.
        """
        try:
            history = await service.get_price_history(
                symbol,
                period=period,
                interval=interval,
            )
        except FinancialDataError as error:
            return (
                str(error),
                {
                    "artifact_type": "price_history",
                    "schema_version": 1,
                    "status": "error",
                    "error": str(error),
                },
            )
        return (
            format_price_history(history),
            {
                "artifact_type": "price_history",
                "schema_version": 1,
                "status": "ok",
                "data": history.to_dict(),
            },
        )

    return get_price_history


def create_financial_statements_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def get_financial_statements(
        symbol: str, frequency: str = "yearly"
    ) -> tuple[str, dict[str, object]]:
        """Get up to four annual/yearly or quarter/quarterly statement periods."""
        try:
            data = await service.get_financial_statements(symbol, frequency=frequency)
        except FinancialDataError as error:
            return _error_artifact("financial_statements", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return get_financial_statements


def create_fundamental_ratios_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def get_fundamental_ratios(
        symbol: str,
    ) -> tuple[str, dict[str, object]]:
        """Get valuation, profitability, return, liquidity, and leverage ratios."""
        try:
            data = await service.get_fundamental_ratios(symbol)
        except FinancialDataError as error:
            return _error_artifact("fundamental_ratios", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return get_fundamental_ratios


def create_analyst_estimates_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def get_analyst_estimates(
        symbol: str,
    ) -> tuple[str, dict[str, object]]:
        """Get analyst EPS, revenue, growth, and price-target estimates."""
        try:
            data = await service.get_analyst_estimates(symbol)
        except FinancialDataError as error:
            return _error_artifact("analyst_estimates", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return get_analyst_estimates


def create_sec_filings_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def get_sec_filings(
        symbol: str, limit: int = 10
    ) -> tuple[str, dict[str, object]]:
        """Get recent SEC filing metadata with direct EDGAR document links."""
        try:
            data = await service.get_sec_filings(symbol, limit=limit)
        except FinancialDataError as error:
            return _error_artifact("sec_filings", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return get_sec_filings


def create_ownership_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def get_ownership(
        symbol: str, limit: int = 10
    ) -> tuple[str, dict[str, object]]:
        """Get aggregate ownership and leading institutional holders."""
        try:
            data = await service.get_ownership(symbol, limit=limit)
        except FinancialDataError as error:
            return _error_artifact("ownership", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return get_ownership


def create_insider_activity_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def get_insider_activity(
        symbol: str, limit: int = 10
    ) -> tuple[str, dict[str, object]]:
        """Get recent reported insider transactions and source links."""
        try:
            data = await service.get_insider_activity(symbol, limit=limit)
        except FinancialDataError as error:
            return _error_artifact("insider_activity", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return get_insider_activity


def create_company_news_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def get_company_news(
        symbol: str, limit: int = 8
    ) -> tuple[str, dict[str, object]]:
        """Get recent company headlines, publishers, timestamps, and links."""
        try:
            data = await service.get_company_news(symbol, limit=limit)
        except FinancialDataError as error:
            return _error_artifact("company_news", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return get_company_news


def create_company_comparison_tool(service: CompanyResearchService) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def compare_companies(
        symbols: list[str],
    ) -> tuple[str, dict[str, object]]:
        """Compare normalized overview metrics for 2 to 5 public tickers."""
        try:
            data = await service.compare_companies(symbols)
        except FinancialDataError as error:
            return _error_artifact("company_comparison", error)
        return format_fundamental_dataset(data), _dataset_artifact(data)

    return compare_companies


def create_default_tools() -> Sequence[BaseTool]:
    """Compose the production tools and their dependencies.

    Returns:
        Complete Yahoo-backed overview, price, and fundamental toolset.
    """
    provider = YahooFinanceProvider()
    service = CompanyResearchService(provider)
    return [
        create_company_overview_tool(service),
        create_price_history_tool(service),
        create_financial_statements_tool(service),
        create_fundamental_ratios_tool(service),
        create_analyst_estimates_tool(service),
        create_sec_filings_tool(service),
        create_ownership_tool(service),
        create_insider_activity_tool(service),
        create_company_news_tool(service),
        create_company_comparison_tool(service),
    ]
