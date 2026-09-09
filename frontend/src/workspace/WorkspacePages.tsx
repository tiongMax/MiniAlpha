import { useState, type FormEvent } from 'react'
import {
  ArrowDownUp,
  ArrowUpRight,
  BookOpen,
  Building2,
  FlaskConical,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import type { usePersonalResearch } from '../personal/usePersonalResearch'
import type { ResearchChatController } from '../chat/ResearchChat'
import type { Artifact } from '../types'
import { ResearchResult } from './ResearchResult'
import { useWatchlistMarketData } from './useWatchlistMarketData'
import {
  comparisonError,
  navigate,
  parseSymbols,
  strategyError,
  SYMBOL_PATTERN,
  type ResearchDocument,
  type ResearchKind,
  type ResearchSettings,
} from './model'

export type PersonalResearch = ReturnType<typeof usePersonalResearch>
export interface ResearchRequest {
  kind: ResearchKind
  title: string
  symbols: string[]
  prompt: string
  settings?: ResearchSettings
}
export interface PageProps {
  target: string
  documents: ResearchDocument[]
  chat: ResearchChatController
  personal: PersonalResearch
  onRun: (request: ResearchRequest) => void
  onOpenAssistant: (threadId: string) => void
}

export function PageIntro({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string
  title: string
  description: string
}) {
  return (
    <header className="page-intro">
      <span className="eyebrow">{eyebrow}</span>
      <h1>{title}</h1>
      <p>{description}</p>
    </header>
  )
}

function TickerForm({ personal }: { personal: PersonalResearch }) {
  const [ticker, setTicker] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <>
      <form
        className="watchlist-form"
        onSubmit={(event) => {
          event.preventDefault()
          const message = personal.addSymbol(ticker)
          setError(message)
          if (!message) setTicker('')
        }}
      >
        <label className="sr-only" htmlFor="watchlist-ticker">
          Add ticker to watchlist
        </label>
        <input
          id="watchlist-ticker"
          value={ticker}
          onChange={(event) => setTicker(event.target.value)}
          placeholder="Add a ticker…"
          maxLength={20}
          required
        />
        <button className="secondary-button">
          <Plus size={15} /> Add
        </button>
      </form>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
    </>
  )
}

export function OverviewPage({ personal, documents, chat, onOpenAssistant }: PageProps) {
  const symbols = personal.watchlist.map((item) => item.symbol)
  const market = useWatchlistMarketData(symbols)
  const marketBySymbol = new Map((market.data?.items ?? []).map((item) => [item.symbol, item]))
  const formatPrice = (price: number, currency: string | null) => {
    if (!currency) return price.toLocaleString(undefined, { maximumFractionDigits: 2 })
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        maximumFractionDigits: 2,
      }).format(price)
    } catch {
      return `${currency} ${price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
    }
  }
  const formatPercent = (value: number) =>
    new Intl.NumberFormat(undefined, {
      style: 'percent',
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }).format(value)
  const formatSignedPercent = (value: number) =>
    new Intl.NumberFormat(undefined, {
      style: 'percent',
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
      signDisplay: 'exceptZero',
    }).format(value)

  return (
    <>
      <PageIntro
        eyebrow="YOUR RESEARCH DESK"
        title="A clearer view of what matters."
        description="Follow your companies, revisit the evidence, and turn questions into research."
      />
      <div className="summary-strip">
        <div>
          <span>Following</span>
          <strong>
            {personal.watchlist.length.toString().padStart(2, '0')}
            <small> companies</small>
          </strong>
        </div>
        <div>
          <span>Saved analyses</span>
          <strong>
            {documents.length.toString().padStart(2, '0')}
            <small> reports</small>
          </strong>
        </div>
        <div>
          <span>Research journal</span>
          <strong>
            {personal.journal.length.toString().padStart(2, '0')}
            <small> notes</small>
          </strong>
        </div>
      </div>
      <div className="workspace-grid">
        <section className="surface watchlist-surface">
          <header className="surface-heading">
            <h2>Your watchlist</h2>
            <button
              className="text-button refresh-market-button"
              disabled={!symbols.length || market.isFetching}
              onClick={() => void market.refetch()}
            >
              <RefreshCw size={12} className={market.isFetching ? 'is-spinning' : ''} />
              {market.isFetching ? 'Refreshing…' : 'Refresh prices'}
            </button>
          </header>
          <TickerForm personal={personal} />
          {personal.watchlist.length ? (
            <div className="watchlist-table">
              <div className="watchlist-row table-label">
                <span>Company</span>
                <span>Price</span>
                <span>Day</span>
                <span>Risk</span>
                <span>As of</span>
                <span />
              </div>
              {personal.watchlist.map((item) => {
                const snapshot = marketBySymbol.get(item.symbol)
                return (
                  <div className="watchlist-row" key={item.symbol}>
                    <button
                      className="ticker-link"
                      onClick={() => navigate('companies', item.symbol)}
                    >
                      <span className="ticker-avatar">{item.symbol.slice(0, 2)}</span>
                      <strong>{item.symbol}</strong>
                      <ArrowUpRight size={13} />
                    </button>
                    <span className="market-price">
                      {snapshot?.status === 'ok'
                        ? formatPrice(snapshot.latest_price, snapshot.currency)
                        : '—'}
                    </span>
                    <span
                      className={
                        snapshot?.status === 'ok'
                          ? snapshot.daily_change_percent > 0
                            ? 'market-change positive'
                            : snapshot.daily_change_percent < 0
                              ? 'market-change negative'
                              : 'market-change'
                          : 'muted'
                      }
                    >
                      {snapshot?.status === 'ok'
                        ? formatSignedPercent(snapshot.daily_change_percent)
                        : snapshot?.status === 'error'
                          ? 'Unavailable'
                          : '—'}
                    </span>
                    <span
                      className="market-risk muted"
                      title="30-day annualized volatility · 3-month maximum drawdown"
                    >
                      {snapshot?.status === 'ok'
                        ? `${snapshot.annualized_volatility_30d === null ? '—' : formatPercent(snapshot.annualized_volatility_30d)} vol · ${formatPercent(snapshot.maximum_drawdown_3m)} DD`
                        : '—'}
                    </span>
                    <span
                      className="market-observation muted"
                      title={
                        snapshot?.status === 'ok'
                          ? new Date(snapshot.latest_observation_at).toLocaleString()
                          : snapshot?.status === 'error'
                            ? snapshot.message
                            : undefined
                      }
                    >
                      {snapshot?.status === 'ok'
                        ? new Date(snapshot.latest_observation_at).toLocaleDateString(undefined, {
                            month: 'short',
                            day: 'numeric',
                          })
                        : '—'}
                    </span>
                    <button
                      className="icon-button remove-button"
                      aria-label={`Remove ${item.symbol} from watchlist`}
                      onClick={() => personal.removeSymbol(item.symbol)}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="small-empty">
              <Building2 size={26} />
              <h3>Start with a company you follow.</h3>
              <p>Add a ticker above to create your personal research desk.</p>
            </div>
          )}
          <p className={`surface-footnote ${market.isError ? 'inline-error' : ''}`}>
            {market.isError
              ? `${market.error.message} Use Refresh prices to try again.`
              : 'Prices refresh every minute while this dashboard is open. Risk shows 30-day annualized volatility and three-month maximum drawdown.'}
          </p>
        </section>
        <section className="surface">
          <header className="surface-heading">
            <h2>Start an analysis</h2>
            <span className="eyebrow">TOOLS</span>
          </header>
          <div className="action-list">
            {[
              {
                page: 'companies' as const,
                Icon: Building2,
                title: 'Company research',
                text: 'Understand the business and its fundamentals.',
              },
              {
                page: 'compare' as const,
                Icon: ArrowDownUp,
                title: 'Compare companies',
                text: 'Put valuation and performance side by side.',
              },
              {
                page: 'strategy' as const,
                Icon: FlaskConical,
                title: 'Strategy lab',
                text: 'Test an idea against historical prices.',
              },
            ].map(({ page, Icon, title, text }) => (
              <button key={page} onClick={() => navigate(page)}>
                <span className="action-icon">
                  <Icon size={20} />
                </span>
                <span>
                  <strong>{title}</strong>
                  <small>{text}</small>
                </span>
                <ArrowUpRight size={17} />
              </button>
            ))}
          </div>
        </section>
      </div>
      <section className="surface">
        <header className="surface-heading">
          <h2>Recent research</h2>
          <span className="muted">Pick up where you left off</span>
        </header>
        {documents.length ? (
          <div className="document-grid">
            {documents.slice(0, 6).map((doc) => (
              <button
                className="document-card"
                key={doc.id}
                onClick={() =>
                  navigate(doc.kind, doc.kind === 'companies' ? doc.symbols[0] : doc.id)
                }
              >
                <span className="document-kind">
                  {doc.kind === 'strategy'
                    ? 'Strategy lab'
                    : doc.kind === 'compare'
                      ? 'Comparison'
                      : 'Company research'}
                  <ArrowUpRight size={15} />
                </span>
                <strong>{doc.title}</strong>
                <span>{doc.symbols.join(' · ')}</span>
                <small>
                  {new Date(doc.updatedAt).toLocaleDateString(undefined, {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                  })}
                </small>
              </button>
            ))}
          </div>
        ) : (
          <p className="muted empty-copy">
            Your saved reports will appear here after you start an analysis.
          </p>
        )}
        {!!chat.threads.length && (
          <details className="recent-conversations">
            <summary>Conversation history</summary>
            {chat.threads.slice(0, 8).map((thread) => (
              <button
                className="history-link"
                key={thread.thread_id}
                disabled={chat.streaming}
                onClick={() => onOpenAssistant(thread.thread_id)}
              >
                {thread.title ?? 'Untitled research'}
                <ArrowUpRight size={14} />
              </button>
            ))}
          </details>
        )}
        {chat.loadingThreads && (
          <p className="muted" role="status">
            Loading conversation history…
          </p>
        )}
        {chat.error && (
          <div className="inline-error" role="alert">
            {chat.error}
            <button className="text-button" onClick={() => void chat.refreshThreads()}>
              Retry
            </button>
          </div>
        )}
      </section>
    </>
  )
}

const companyTabs = ['Overview', 'Financials', 'News & filings', 'Risk & technicals'] as const
type CompanyTab = (typeof companyTabs)[number]
const tabArtifacts: Record<CompanyTab, string[]> = {
  Overview: ['company_overview', 'price_history', 'company_comparison'],
  Financials: [
    'financial_statements',
    'fundamental_ratios',
    'analyst_estimates',
    'ownership',
    'insider_activity',
  ],
  'News & filings': ['company_news', 'sec_filings'],
  'Risk & technicals': [
    'return_statistics',
    'volatility_analysis',
    'drawdown_analysis',
    'technical_indicators',
  ],
}

export function CompanyPage(props: PageProps) {
  const { target, personal, documents, chat, onRun, onOpenAssistant } = props
  const symbol = target.toUpperCase()
  const [input, setInput] = useState(symbol)
  const lastSection = documents.find(
    (doc) => doc.kind === 'companies' && doc.symbols.includes(symbol),
  )?.settings?.section
  const [tab, setTab] = useState<CompanyTab>(
    companyTabs.includes(lastSection as CompanyTab) ? (lastSection as CompanyTab) : 'Overview',
  )
  const [period, setPeriod] = useState('1y')
  const [error, setError] = useState<string | null>(null)
  const relevant = documents.filter((doc) => doc.symbols.includes(symbol))
  const document =
    relevant.find((doc) => doc.kind === 'companies' && doc.title.includes(tab)) ??
    relevant.find((doc) => doc.kind === 'companies') ??
    relevant[0]
  const filter = (artifact: Artifact) => tabArtifacts[tab].includes(artifact.artifact_type)
  const run = () => {
    if (!SYMBOL_PATTERN.test(symbol)) {
      setError('Choose a valid ticker first.')
      return
    }
    const requests: Record<CompanyTab, string> = {
      Overview: `Retrieve the company overview and ${period} daily price history for ${symbol}. Explain its business and key valuation metrics.`,
      Financials: `Research ${symbol}: retrieve financial statements, fundamental ratios and analyst estimates. Explain growth, profitability and balance sheet risks.`,
      'News & filings': `Retrieve recent company news and SEC filings for ${symbol}. Summarize the material developments with source links.`,
      'Risk & technicals': `For ${symbol}, calculate return statistics, volatility, drawdowns and technical indicators over ${period} using daily data. Explain the results.`,
    }
    onRun({
      kind: 'companies',
      title: `${symbol} · ${tab}`,
      symbols: [symbol],
      prompt: requests[tab],
      settings: { section: tab, period },
    })
  }
  return (
    <>
      <PageIntro
        eyebrow="COMPANY WORKSPACE"
        title={symbol || 'Know the company.'}
        description="Bring the business, its numbers, and your research together in one place."
      />
      <form
        className="surface company-search"
        onSubmit={(event) => {
          event.preventDefault()
          const next = input.trim().toUpperCase()
          if (!SYMBOL_PATTERN.test(next)) {
            setError('Enter a ticker such as AAPL, ^GSPC, or 1155.KL.')
            return
          }
          setError(null)
          navigate('companies', next)
        }}
      >
        <label htmlFor="company-symbol">Company ticker</label>
        <input
          id="company-symbol"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="e.g. AAPL"
          maxLength={20}
          required
        />
        <button className="primary-button">
          Open workspace <ArrowUpRight size={15} />
        </button>
      </form>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {symbol ? (
        <>
          <div className="company-toolbar">
            <div className="segmented-tabs" aria-label="Company views">
              {companyTabs.map((label) => (
                <button key={label} aria-pressed={tab === label} onClick={() => setTab(label)}>
                  {label}
                </button>
              ))}
            </div>
            <button
              className="secondary-button"
              disabled={personal.watchlist.some((item) => item.symbol === symbol)}
              onClick={() => setError(personal.addSymbol(symbol))}
            >
              <Plus size={14} />{' '}
              {personal.watchlist.some((item) => item.symbol === symbol) ? 'Following' : 'Follow'}
            </button>
          </div>
          <div className="analysis-toolbar">
            <span className="muted">
              {tab === 'Overview'
                ? 'Company snapshot and historical prices'
                : tab === 'Financials'
                  ? 'Statements, ratios and estimates'
                  : tab === 'News & filings'
                    ? 'Recent developments and primary sources'
                    : 'Historical returns, drawdowns and indicators'}
            </span>
            <div>
              {(tab === 'Overview' || tab === 'Risk & technicals') && (
                <label className="inline-field">
                  Period
                  <select value={period} onChange={(event) => setPeriod(event.target.value)}>
                    <option value="3mo">3 months</option>
                    <option value="6mo">6 months</option>
                    <option value="1y">1 year</option>
                    <option value="2y">2 years</option>
                    <option value="5y">5 years</option>
                  </select>
                </label>
              )}
              <button className="primary-button" disabled={chat.streaming} onClick={run}>
                {chat.streaming ? 'Research running…' : `Research ${tab.toLowerCase()}`}
              </button>
            </div>
          </div>
          <ResearchResult
            document={document}
            chat={chat}
            symbol={symbol}
            filter={filter}
            onOpenAssistant={onOpenAssistant}
          />
          <section className="surface thesis-preview">
            <BookOpen size={18} />
            <div>
              <h3>Your research journal</h3>
              <p>
                {personal.journal.find((entry) => entry.symbol === symbol)?.note ??
                  'Capture your thesis, the risks, and what would change your mind.'}
              </p>
            </div>
            <button className="text-button" onClick={() => navigate('journal', symbol)}>
              Open journal <ArrowUpRight size={14} />
            </button>
          </section>
        </>
      ) : (
        <div className="workspace-empty">
          <Building2 size={32} />
          <h3>Choose your starting point.</h3>
          <p>Enter a ticker above, or open a company from your watchlist.</p>
          <div className="ticker-chips">
            {personal.watchlist.map((item) => (
              <button
                className="secondary-button"
                key={item.symbol}
                onClick={() => navigate('companies', item.symbol)}
              >
                {item.symbol}
                <ArrowUpRight size={13} />
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

export function ComparePage({ target, documents, chat, onRun, onOpenAssistant }: PageProps) {
  const document = documents.find((doc) => doc.kind === 'compare' && doc.id === target)
  const [input, setInput] = useState(document?.symbols.join(', ') ?? '')
  const [focus, setFocus] = useState(document?.settings?.focus ?? 'Valuation and fundamentals')
  const [error, setError] = useState<string | null>(null)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const symbols = parseSymbols(input)
    const message = comparisonError(symbols)
    setError(message)
    if (!message)
      onRun({
        kind: 'compare',
        title: symbols.join(' vs '),
        symbols,
        settings: { focus },
        prompt: `Compare ${symbols.join(', ')}. Focus on ${focus}. Retrieve a structured company comparison${focus === 'Returns and correlation' ? ' and calculate return statistics and correlations over 1y with daily data' : ''}. Explain differences, data limitations, and key risks. Do not invent unavailable values.`,
      })
  }
  return (
    <>
      <PageIntro
        eyebrow="COMPARISON BUILDER"
        title="Put your options side by side."
        description="Compare up to five companies using consistent metrics and traceable evidence."
      />
      <form className="surface analysis-form" onSubmit={submit}>
        <div className="form-grid">
          <label>
            Company tickers
            <input
              aria-label="Company tickers"
              aria-describedby="comparison-ticker-help"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="AAPL, MSFT, GOOGL"
              required
              maxLength={110}
            />
            <small id="comparison-ticker-help">Separate tickers with commas or spaces.</small>
          </label>
          <label>
            Research focus
            <select value={focus} onChange={(event) => setFocus(event.target.value)}>
              <option>Valuation and fundamentals</option>
              <option>Growth and profitability</option>
              <option>Returns and correlation</option>
            </select>
          </label>
        </div>
        <div className="form-footer">
          <span>2–5 companies · Provider-sourced metrics</span>
          <button className="primary-button" disabled={chat.streaming}>
            <ArrowDownUp size={15} /> {chat.streaming ? 'Research running…' : 'Run comparison'}
          </button>
        </div>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
      </form>
      <ResearchResult document={document} chat={chat} onOpenAssistant={onOpenAssistant} />
      <SavedAnalyses documents={documents.filter((doc) => doc.kind === 'compare')} />
    </>
  )
}

export function StrategyPage({ target, documents, chat, onRun, onOpenAssistant }: PageProps) {
  const document = documents.find((doc) => doc.kind === 'strategy' && doc.id === target)
  const [symbol, setSymbol] = useState(document?.symbols[0] ?? '')
  const [period, setPeriod] = useState(document?.settings?.period ?? '2y')
  const [short, setShort] = useState(String(document?.settings?.shortWindow ?? 20))
  const [long, setLong] = useState(String(document?.settings?.longWindow ?? 50))
  const [cost, setCost] = useState(String(document?.settings?.transactionCostBps ?? 10))
  const [error, setError] = useState<string | null>(null)
  return (
    <>
      <PageIntro
        eyebrow="STRATEGY LAB"
        title="Give your idea a history."
        description="Explore a moving-average crossover strategy against a buy-and-hold benchmark."
      />
      <form
        className="surface analysis-form"
        onSubmit={(event) => {
          event.preventDefault()
          const ticker = symbol.trim().toUpperCase()
          const message = !SYMBOL_PATTERN.test(ticker)
            ? 'Enter a valid ticker.'
            : strategyError(Number(short), Number(long), Number(cost))
          setError(message)
          if (!message)
            onRun({
              kind: 'strategy',
              title: `${ticker} · ${short}/${long} crossover · ${period} · ${cost} bps`,
              symbols: [ticker],
              settings: {
                period,
                shortWindow: Number(short),
                longWindow: Number(long),
                transactionCostBps: Number(cost),
              },
              prompt: `Use backtest_moving_average for ${ticker} with period=${period}, interval=1d, short_window=${short}, long_window=${long}, transaction_cost_bps=${cost}. Return the structured moving_average_backtest artifact. Explain the strategy versus its buy-and-hold benchmark, drawdowns and trade counts. Use exactly these parameters; if unavailable, report the limitation.`,
            })
        }}
      >
        <div className="strategy-description">
          <span className="action-icon">
            <FlaskConical size={22} />
          </span>
          <div>
            <strong>Simple moving-average crossover</strong>
            <p>
              Long when the short average exceeds the long average; otherwise cash. Signals take
              effect one period later.
            </p>
          </div>
          <span className="subtle-badge">Daily prices</span>
        </div>
        <div className="form-grid strategy-fields">
          <label>
            Ticker
            <input
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              placeholder="AAPL"
              maxLength={20}
              required
            />
          </label>
          <label>
            Historical period
            <select value={period} onChange={(event) => setPeriod(event.target.value)}>
              <option value="1y">1 year</option>
              <option value="2y">2 years</option>
              <option value="5y">5 years</option>
            </select>
          </label>
          <label>
            Short window (days)
            <input
              type="number"
              min={2}
              max={199}
              step={1}
              value={short}
              onChange={(event) => setShort(event.target.value)}
              required
            />
          </label>
          <label>
            Long window (days)
            <input
              type="number"
              min={3}
              max={200}
              step={1}
              value={long}
              onChange={(event) => setLong(event.target.value)}
              required
            />
          </label>
          <label>
            Cost per trade (bps)
            <input
              type="number"
              min={0}
              max={1000}
              step="any"
              value={cost}
              onChange={(event) => setCost(event.target.value)}
              required
            />
          </label>
        </div>
        <div className="form-footer">
          <span>10 basis points = 0.10% per position change</span>
          <button className="primary-button" disabled={chat.streaming}>
            <FlaskConical size={15} /> {chat.streaming ? 'Research running…' : 'Run backtest'}
          </button>
        </div>
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
      </form>
      <ResearchResult document={document} chat={chat} onOpenAssistant={onOpenAssistant} />
      <p className="method-note">
        Historical simulation with a one-period signal lag and configured transaction costs. Taxes,
        liquidity constraints, and additional slippage are not modeled. Results describe the past.
      </p>
      <SavedAnalyses documents={documents.filter((doc) => doc.kind === 'strategy')} />
    </>
  )
}

function SavedAnalyses({ documents }: { documents: ResearchDocument[] }) {
  if (!documents.length) return null
  return (
    <section className="surface">
      <header className="surface-heading">
        <h2>Saved analyses</h2>
      </header>
      {documents.map((doc) => (
        <button className="history-link" key={doc.id} onClick={() => navigate(doc.kind, doc.id)}>
          {doc.title}
          <ArrowUpRight size={15} />
        </button>
      ))}
    </section>
  )
}

export function JournalPage({ target, personal }: PageProps) {
  const [symbol, setSymbol] = useState(target || personal.watchlist[0]?.symbol || '')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const symbols = [
    ...new Set([
      ...personal.watchlist.map((item) => item.symbol),
      ...personal.journal.map((entry) => entry.symbol),
      ...(target && SYMBOL_PATTERN.test(target) ? [target] : []),
    ]),
  ]
  const entries = personal.journal.filter((entry) => !symbol || entry.symbol === symbol)
  return (
    <>
      <PageIntro
        eyebrow="RESEARCH JOURNAL"
        title="Write down your conviction."
        description="Keep a record of your thesis, the evidence behind it, and what would change your mind."
      />
      <div className="workspace-grid journal-layout">
        <section className="surface">
          <header className="surface-heading">
            <h2>New research note</h2>
            <span className="subtle-badge">On this device</span>
          </header>
          <TickerForm personal={personal} />
          <form
            className="note-form"
            onSubmit={(event) => {
              event.preventDefault()
              const message = personal.addJournalEntry(symbol, note)
              setError(message)
              if (!message) {
                setNote('')
                setSaved(true)
              }
            }}
          >
            <label>
              Company
              <select
                value={symbol}
                onChange={(event) => {
                  setSymbol(event.target.value)
                  setSaved(false)
                }}
                required
              >
                <option value="">Select a company</option>
                {symbols.map((ticker) => (
                  <option key={ticker}>{ticker}</option>
                ))}
              </select>
            </label>
            <label>
              Your thesis
              <textarea
                value={note}
                onChange={(event) => {
                  setNote(event.target.value)
                  setSaved(false)
                }}
                placeholder={
                  'What do you believe?\nWhat evidence supports it?\nWhat would change your mind?'
                }
                rows={9}
                maxLength={10000}
                required
              />
            </label>
            <button className="primary-button" disabled={!symbol || !note.trim()}>
              <Plus size={15} /> Save note
            </button>
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
            {saved && (
              <p className="save-status" role="status">
                Note saved.
              </p>
            )}
          </form>
        </section>
        <section className="journal-history">
          <header className="surface-heading">
            <h2>{symbol ? `${symbol} notes` : 'All notes'}</h2>
            <span className="muted">{entries.length} entries</span>
          </header>
          {entries.length ? (
            entries.map((entry) => (
              <article className="surface journal-note" key={entry.id}>
                <header>
                  <button
                    className="text-button"
                    onClick={() => navigate('companies', entry.symbol)}
                  >
                    {entry.symbol}
                    <ArrowUpRight size={13} />
                  </button>
                  <time dateTime={entry.createdAt}>
                    {new Date(entry.createdAt).toLocaleString(undefined, {
                      dateStyle: 'medium',
                      timeStyle: 'short',
                    })}
                  </time>
                  <button
                    className="icon-button remove-button"
                    aria-label={`Delete note for ${entry.symbol}`}
                    onClick={() => personal.removeJournalEntry(entry.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </header>
                <p>{entry.note}</p>
              </article>
            ))
          ) : (
            <div className="small-empty">
              <BookOpen size={28} />
              <h3>Leave a trail of your thinking.</h3>
              <p>Your notes for this company will appear here.</p>
            </div>
          )}
        </section>
      </div>
    </>
  )
}
