import { useQuery } from '@tanstack/react-query'
import { loadWatchlistMarketData } from '../api/client'
import { queryKeys } from '../lib/queryKeys'

export function useWatchlistMarketData(symbols: string[]) {
  return useQuery({
    queryKey: queryKeys.market.watchlist(symbols),
    queryFn: ({ signal }) => loadWatchlistMarketData(symbols, signal),
    enabled: symbols.length > 0,
    staleTime: 45_000,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })
}
