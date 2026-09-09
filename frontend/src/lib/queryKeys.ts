export const queryKeys = {
  market: {
    all: ['market'] as const,
    watchlist: (symbols: string[]) => [...queryKeys.market.all, 'watchlist', ...symbols] as const,
  },
  threads: {
    all: ['threads'] as const,
    list: () => [...queryKeys.threads.all, 'list'] as const,
    detail: (threadId: string) => [...queryKeys.threads.all, 'detail', threadId] as const,
  },
}
