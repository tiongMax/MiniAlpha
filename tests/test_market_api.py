"""HTTP contracts for batch watchlist market data."""

from app.api.main import create_app
from app.services.company_research import CompanyResearchService
from app.services.watchlist_market import WatchlistMarketService
from tests.api_helpers import api_request, research_service
from tests.test_research_agent import SuccessfulGraph
from tests.test_watchlist_market import PriceProvider


def app_with_market_data():
    """Create an API with deterministic research and market services."""
    market = WatchlistMarketService(CompanyResearchService(PriceProvider()))  # type: ignore[arg-type]
    return create_app(
        research_service(SuccessfulGraph()),
        watchlist_market_service=market,
    )


def test_watchlist_endpoint_returns_partial_batch_results() -> None:
    """The API exposes successful and failed tickers in request order."""
    response = api_request(
        app_with_market_data(),
        "POST",
        "/api/v1/market/watchlist",
        json={"symbols": ["msft", "MISSING"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["symbol"] == "MSFT"
    assert payload["items"][0]["currency"] == "USD"
    assert payload["items"][0]["status"] == "ok"
    assert payload["items"][1] == {
        "status": "error",
        "symbol": "MISSING",
        "code": "symbol_not_found",
        "message": "No market data was found for MISSING.",
    }


def test_watchlist_endpoint_rejects_invalid_or_oversized_batches() -> None:
    """The endpoint bounds upstream work before entering the service."""
    invalid = api_request(
        app_with_market_data(),
        "POST",
        "/api/v1/market/watchlist",
        json={"symbols": ["not a ticker"]},
    )
    oversized = api_request(
        app_with_market_data(),
        "POST",
        "/api/v1/market/watchlist",
        json={"symbols": [f"T{index}" for index in range(26)]},
    )

    assert invalid.status_code == 422
    assert oversized.status_code == 422
