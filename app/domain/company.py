"""Normalized company data used independently of any upstream provider."""

from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CompanyOverview:
    """Provider-neutral snapshot of public-company financial data.

    Monetary fields contain raw values in ``currency`` rather than formatted
    strings. Growth rates, margins, and dividend yield are decimal fractions:
    for example, ``0.125`` represents 12.5%. Optional fields remain ``None``
    when the upstream provider does not supply a value.

    Attributes:
        symbol: Normalized exchange ticker, such as ``AAPL`` or ``BRK-B``.
        company_name: Provider-reported legal or commonly used company name.
        exchange: Exchange name or provider-specific exchange identifier.
        currency: ISO-style trading/reporting currency code, such as ``USD``.
        sector: Broad economic sector assigned by the provider.
        industry: More specific industry assigned by the provider.
        price: Latest provider-reported share price in ``currency``.
        market_cap: Equity market capitalization in ``currency``.
        trailing_pe: Price-to-earnings ratio based on trailing earnings.
        forward_pe: Price-to-earnings ratio based on forecast earnings.
        price_to_book: Market price divided by book value per share.
        total_revenue: Provider-reported revenue for its current reference
            period, in ``currency``.
        revenue_growth: Revenue growth as a decimal fraction.
        operating_margin: Operating income divided by revenue, as a decimal
            fraction.
        profit_margin: Net income divided by revenue, as a decimal fraction.
        total_cash: Cash and cash-equivalent balance in ``currency``.
        total_debt: Total debt balance in ``currency``.
        dividend_yield: Trailing annual dividend yield as a decimal fraction.
        beta: Provider-reported measure of price sensitivity to the market.
        provider: Human-readable source name.
        retrieved_at: Timezone-aware UTC time when MiniAlpha retrieved data.
    """

    symbol: str
    company_name: str | None
    exchange: str | None
    currency: str | None
    sector: str | None
    industry: str | None
    price: float | None
    market_cap: float | None
    trailing_pe: float | None
    forward_pe: float | None
    price_to_book: float | None
    total_revenue: float | None
    revenue_growth: float | None
    operating_margin: float | None
    profit_margin: float | None
    total_cash: float | None
    total_debt: float | None
    dividend_yield: float | None
    beta: float | None
    provider: str
    retrieved_at: datetime

    def to_dict(self) -> dict[str, object]:
        """Convert the snapshot into an artifact-safe dictionary.

        Returns:
            A dictionary containing every dataclass field. ``retrieved_at`` is
            converted to an ISO 8601 string; numeric and missing values retain
            their original types.
        """
        data: dict[str, object] = asdict(self)
        data["retrieved_at"] = self.retrieved_at.isoformat()
        return data
