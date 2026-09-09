import type { Artifact } from '../types'

export const SYMBOL_PATTERN = /^[A-Z0-9^][A-Z0-9.^=-]{0,19}$/
export const pages = [
  'overview',
  'companies',
  'compare',
  'strategy',
  'journal',
  'assistant',
] as const
export type Page = (typeof pages)[number]
export type ResearchKind = 'companies' | 'compare' | 'strategy'
export interface WorkspaceRoute {
  page: Page
  target: string
}
export interface ResearchSettings {
  section?: string
  focus?: string
  period?: string
  shortWindow?: number
  longWindow?: number
  transactionCostBps?: number
}
export interface ResearchDocument {
  id: string
  kind: ResearchKind
  title: string
  symbols: string[]
  threadId: string
  updatedAt: string
  settings?: ResearchSettings
}

export function readRoute(hash: string): WorkspaceRoute {
  const [page, raw = ''] = hash.replace(/^#\/?/, '').split('/')
  let target = ''
  try {
    target = decodeURIComponent(raw)
  } catch {
    /* An invalid URL opens the page without a selection. */
  }
  const resolvedPage = pages.includes(page as Page) ? (page as Page) : 'overview'
  if (
    (resolvedPage === 'companies' || resolvedPage === 'journal') &&
    target &&
    !SYMBOL_PATTERN.test(target.toUpperCase())
  )
    target = ''
  return { page: resolvedPage, target }
}

export function navigate(page: Page, target = '') {
  window.location.hash = `/${page}${target ? `/${encodeURIComponent(target)}` : ''}`
}

export function parseSymbols(input: string): string[] {
  return [
    ...new Set(
      input
        .toUpperCase()
        .split(/[\s,]+/)
        .filter(Boolean),
    ),
  ]
}

export function comparisonError(symbols: string[]): string | null {
  if (symbols.length < 2 || symbols.length > 5) return 'Choose between 2 and 5 distinct tickers.'
  if (symbols.some((symbol) => !SYMBOL_PATTERN.test(symbol)))
    return 'Use valid tickers, such as AAPL, MSFT, or 1155.KL.'
  return null
}

export function strategyError(short: number, long: number, cost: number): string | null {
  if (
    !Number.isInteger(short) ||
    !Number.isInteger(long) ||
    short < 2 ||
    short >= long ||
    long > 200
  ) {
    return 'Moving averages must satisfy 2 ≤ short window < long window ≤ 200.'
  }
  if (!Number.isFinite(cost) || cost < 0 || cost > 1000)
    return 'Transaction cost must be between 0 and 1,000 basis points.'
  return null
}

export function artifactSymbols(artifact: Artifact): string[] {
  const data = artifact.data
  if (!data) return []
  if (typeof data.symbol === 'string') return [data.symbol]
  if (Array.isArray(data.symbols))
    return data.symbols.filter((symbol): symbol is string => typeof symbol === 'string')
  if (Array.isArray(data.records))
    return data.records.flatMap((record: unknown) =>
      record &&
      typeof record === 'object' &&
      'symbol' in record &&
      typeof record.symbol === 'string'
        ? [record.symbol]
        : [],
    )
  return []
}

export function isResearchDocument(value: unknown): value is ResearchDocument {
  if (!value || typeof value !== 'object') return false
  const doc = value as Partial<ResearchDocument>
  if (doc.settings !== undefined) {
    if (!doc.settings || typeof doc.settings !== 'object' || Array.isArray(doc.settings))
      return false
    const { section, focus, period, shortWindow, longWindow, transactionCostBps } = doc.settings
    if ([section, focus, period].some((item) => item !== undefined && typeof item !== 'string'))
      return false
    if (
      [shortWindow, longWindow, transactionCostBps].some(
        (item) => item !== undefined && (typeof item !== 'number' || !Number.isFinite(item)),
      )
    )
      return false
  }
  return (
    typeof doc.id === 'string' &&
    ['companies', 'compare', 'strategy'].includes(doc.kind ?? '') &&
    typeof doc.title === 'string' &&
    typeof doc.threadId === 'string' &&
    /^[0-9a-f-]{36}$/i.test(doc.threadId) &&
    typeof doc.updatedAt === 'string' &&
    Number.isFinite(Date.parse(doc.updatedAt)) &&
    Array.isArray(doc.symbols) &&
    doc.symbols.length > 0 &&
    doc.symbols.every((symbol) => typeof symbol === 'string' && SYMBOL_PATTERN.test(symbol))
  )
}
