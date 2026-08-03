"""Deterministic tools used to learn the model-tool loop."""

from langchain_core.tools import tool


_COMPANIES = {
    "AAPL": {
        "name": "Apple Inc.",
        "price": 220.00,
        "market_cap": "$3.3T",
        "revenue_growth": "6.4%",
        "operating_margin": "31.7%",
    },
    "MSFT": {
        "name": "Microsoft Corporation",
        "price": 510.00,
        "market_cap": "$3.8T",
        "revenue_growth": "15.2%",
        "operating_margin": "45.6%",
    },
}


@tool
async def get_company_overview(symbol: str) -> str:
    """Get a basic investment overview for a publicly traded company."""
    normalized_symbol = symbol.strip().upper()
    company = _COMPANIES.get(normalized_symbol)

    if company is None:
        return (
            f"No Phase 1 sample data is available for {normalized_symbol}. "
            "The fake provider currently contains only AAPL and MSFT."
        )

    return "\n".join(
        (
            f"{company['name']} ({normalized_symbol})",
            f"Price: ${company['price']:.2f}",
            f"Market capitalization: {company['market_cap']}",
            f"Revenue growth: {company['revenue_growth']}",
            f"Operating margin: {company['operating_margin']}",
            "Source: deterministic Phase 1 sample data",
        )
    )


PHASE_ONE_TOOLS = [get_company_overview]

