"""Batch market-data routes for the product watchlist."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_watchlist_market_service
from app.api.schemas import WatchlistMarketRequest, WatchlistMarketResponse
from app.services.watchlist_market import WatchlistMarketService

router = APIRouter(prefix="/api/v1/market", tags=["market"])


@router.post(
    "/watchlist",
    response_model=WatchlistMarketResponse,
    summary="Refresh market data for a watchlist",
)
async def refresh_watchlist(
    request: WatchlistMarketRequest,
    service: Annotated[WatchlistMarketService, Depends(get_watchlist_market_service)],
) -> WatchlistMarketResponse:
    """Fetch one ordered batch while isolating failures by symbol."""
    items = await service.get_snapshots(request.symbols)
    return WatchlistMarketResponse.model_validate(
        {"items": [asdict(item) for item in items]}
    )
