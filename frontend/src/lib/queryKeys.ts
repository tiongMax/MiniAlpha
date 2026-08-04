export const queryKeys = {
  threads: {
    all: ['threads'] as const,
    list: () => [...queryKeys.threads.all, 'list'] as const,
    detail: (threadId: string) => [...queryKeys.threads.all, 'detail', threadId] as const,
  },
}
