"""Contracts for the provider-neutral live-experiment tool surface."""

from evals.synthetic_finance import create_synthetic_financial_tools


def test_synthetic_tool_surface_matches_production() -> None:
    assert {tool.name for tool in create_synthetic_financial_tools()} == {
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
        "calculate_return_statistics",
        "calculate_volatility",
        "analyze_drawdowns",
        "calculate_correlations",
        "calculate_technical_indicators",
        "backtest_moving_average",
    }
