import { useCallback, useEffect, useState } from 'react'
import { SYMBOL_PATTERN } from '../workspace/model'

const STORAGE_KEY = 'minialpha.personal-research.v1'

export interface WatchlistItem {
  symbol: string
  addedAt: string
}

export interface JournalEntry {
  id: string
  symbol: string
  note: string
  createdAt: string
}

interface PersonalResearchState {
  watchlist: WatchlistItem[]
  journal: JournalEntry[]
}

const EMPTY_STATE: PersonalResearchState = { watchlist: [], journal: [] }

function readState(): PersonalResearchState {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return EMPTY_STATE
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return EMPTY_STATE
    const candidate = parsed as Partial<PersonalResearchState>
    return {
      watchlist: Array.isArray(candidate.watchlist)
        ? candidate.watchlist.filter(
            (item) =>
              item &&
              typeof item.symbol === 'string' &&
              SYMBOL_PATTERN.test(item.symbol) &&
              typeof item.addedAt === 'string' &&
              Number.isFinite(Date.parse(item.addedAt)),
          )
        : [],
      journal: Array.isArray(candidate.journal)
        ? candidate.journal.filter(
            (item) =>
              item &&
              typeof item.id === 'string' &&
              typeof item.symbol === 'string' &&
              SYMBOL_PATTERN.test(item.symbol) &&
              typeof item.note === 'string' &&
              typeof item.createdAt === 'string' &&
              Number.isFinite(Date.parse(item.createdAt)),
          )
        : [],
    }
  } catch {
    return EMPTY_STATE
  }
}

export function usePersonalResearch() {
  const [state, setState] = useState<PersonalResearchState>(readState)
  const [storageError, setStorageError] = useState<string | null>(null)

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
      setStorageError(null)
    } catch {
      setStorageError(
        'Changes are available for this session only because browser storage is unavailable.',
      )
    }
  }, [state])

  const addSymbol = useCallback((rawSymbol: string): string | null => {
    const symbol = rawSymbol.trim().toUpperCase()
    if (!SYMBOL_PATTERN.test(symbol)) {
      return 'Enter a ticker such as AAPL, ^GSPC, or 1155.KL.'
    }
    setState((current) =>
      current.watchlist.some((item) => item.symbol === symbol)
        ? current
        : {
            ...current,
            watchlist: [...current.watchlist, { symbol, addedAt: new Date().toISOString() }],
          },
    )
    return null
  }, [])

  const removeSymbol = useCallback((symbol: string) => {
    setState((current) => ({
      ...current,
      watchlist: current.watchlist.filter((item) => item.symbol !== symbol),
    }))
  }, [])

  const addJournalEntry = useCallback((symbol: string, rawNote: string): string | null => {
    const note = rawNote.trim()
    if (!symbol) return 'Add a ticker to your watchlist first.'
    if (!note) return 'Write a note before saving.'
    setState((current) => ({
      ...current,
      journal: [
        {
          id: crypto.randomUUID(),
          symbol,
          note,
          createdAt: new Date().toISOString(),
        },
        ...current.journal,
      ],
    }))
    return null
  }, [])

  const removeJournalEntry = useCallback((id: string) => {
    setState((current) => ({
      ...current,
      journal: current.journal.filter((entry) => entry.id !== id),
    }))
  }, [])

  return {
    ...state,
    storageError,
    addSymbol,
    removeSymbol,
    addJournalEntry,
    removeJournalEntry,
  }
}
