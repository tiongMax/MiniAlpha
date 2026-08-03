"""Exercise the real provider without invoking Gemini."""

import asyncio
import sys

from app.domain.errors import FinancialDataError
from app.providers.yahoo import YahooFinanceProvider
from app.services.company_research import CompanyResearchService


async def main(symbols: list[str]) -> None:
    """Fetch and print concise real-provider results for several tickers.

    Args:
        symbols: Raw ticker symbols to validate and request from Yahoo.

    Returns:
        ``None``. Each success or expected financial-data error is written to
        standard output.
    """
    service = CompanyResearchService(YahooFinanceProvider())

    for symbol in symbols:
        try:
            overview = await service.get_company_overview(symbol)
        except FinancialDataError as error:
            print(f"{symbol}: ERROR - {error}")
            continue

        print(
            f"{overview.symbol}: {overview.company_name} | "
            f"price={overview.price} {overview.currency} | "
            f"market_cap={overview.market_cap} | "
            f"source={overview.provider}"
        )


if __name__ == "__main__":
    requested_symbols = sys.argv[1:] or ["AAPL", "MSFT", "BRK-B"]
    asyncio.run(main(requested_symbols))
