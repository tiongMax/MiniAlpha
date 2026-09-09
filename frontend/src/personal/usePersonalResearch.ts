import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'minialpha.personal-research.v1'
const SYMBOL_PATTERN = /^[A-Z0-9^][A-Z0-9.^=-]{0,19}$/

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
      watchlist: Array.isArray(candidate.watchlist) ? candidate.watchlist : [],
      journal: Array.isArray(candidate.journal) ? candidate.journal : [],
    }
  } catch {
    return EMPTY_STATE
  }
}

export function usePersonalResearch() {
  const [state, setState] = useState<PersonalResearchState>(readState)

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    } catch {
      // Personal research remains usable for the session when storage is blocked.
    }
  }, [state])

  const addSymbol = useCallback((rawSymbol: string): string | null => {
    const symbol = rawSymbol.trim().toUpperCase()
    if (!SYMBOL_PATTERN.test(symbol)) {
      return 'Enter a ticker such as AAPL, ^GSPC, or 1155.KL.'
    }
    setState((current) => current.watchlist.some((item) => item.symbol === symbol)
      ? current
      : {
          ...current,
          watchlist: [...current.watchlist, { symbol, addedAt: new Date().toISOString() }],
        })
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
      journal: [{
        id: crypto.randomUUID(),
        symbol,
        note,
        createdAt: new Date().toISOString(),
      }, ...current.journal],
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
    addSymbol,
    removeSymbol,
    addJournalEntry,
    removeJournalEntry,
  }
}
