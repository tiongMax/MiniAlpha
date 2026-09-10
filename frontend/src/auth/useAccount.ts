import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getCurrentAccount, loginAccount, logoutAccount, registerAccount } from '../api/client'
import { queryKeys } from '../lib/queryKeys'
import type { Account } from '../types'

export function useAccount() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: queryKeys.account,
    queryFn: ({ signal }) => getCurrentAccount(signal),
    retry: false,
    staleTime: 300_000,
  })
  const setAccount = (account: Account | null) =>
    queryClient.setQueryData<Account | null>(queryKeys.account, account)

  return {
    account: query.data ?? null,
    loading: query.isLoading,
    unavailable: query.isError,
    login: async (email: string, password: string) => {
      const account = await loginAccount({ email, password })
      setAccount(account)
    },
    register: async (displayName: string, email: string, password: string) => {
      const account = await registerAccount({
        display_name: displayName,
        email,
        password,
      })
      setAccount(account)
    },
    logout: async () => {
      await logoutAccount()
      setAccount(null)
    },
  }
}

export type AccountController = ReturnType<typeof useAccount>
