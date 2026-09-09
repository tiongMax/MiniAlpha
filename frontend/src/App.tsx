import { useEffect, useState } from 'react'
import {
  Activity,
  ArrowDownUp,
  ArrowUpRight,
  BookOpen,
  Bot,
  Building2,
  FlaskConical,
  LayoutDashboard,
  Menu,
  Search,
  X,
} from 'lucide-react'
import { useResearchChat } from './chat/useResearchChat'
import { ResearchChat } from './chat/ResearchChat'
import { usePersonalResearch } from './personal/usePersonalResearch'
import { useWorkspace } from './workspace/useWorkspace'
import { navigate, SYMBOL_PATTERN } from './workspace/model'
import {
  CompanyPage,
  ComparePage,
  JournalPage,
  OverviewPage,
  StrategyPage,
  type PageProps,
  type ResearchRequest,
} from './workspace/WorkspacePages'

const navigation = [
  { page: 'overview', label: 'Overview', Icon: LayoutDashboard },
  { page: 'companies', label: 'Companies', Icon: Building2 },
  { page: 'compare', label: 'Compare', Icon: ArrowDownUp },
  { page: 'strategy', label: 'Strategy lab', Icon: FlaskConical },
  { page: 'journal', label: 'Journal', Icon: BookOpen },
  { page: 'assistant', label: 'Research assistant', Icon: Bot },
] as const

export default function App() {
  const chat = useResearchChat()
  const personal = usePersonalResearch()
  const workspace = useWorkspace()
  const { page, target } = workspace.route
  const [mobileOpen, setMobileOpen] = useState(false)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [admitting, setAdmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (mobileOpen) {
        setMobileOpen(false)
        document.querySelector<HTMLButtonElement>('.mobile-menu')?.focus()
      } else if (assistantOpen) {
        setAssistantOpen(false)
        document.querySelector<HTMLButtonElement>('.assistant-toggle')?.focus()
      }
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [mobileOpen, assistantOpen])

  useEffect(() => {
    setMobileOpen(false)
    document.title = `${navigation.find((item) => item.page === page)?.label ?? 'Overview'} · MiniAlpha`
    document.getElementById('workspace-heading')?.focus()
  }, [page, target])

  const openAssistant = (threadId: string) => {
    if (chat.streaming && chat.threadId !== threadId) {
      setNotice('Finish or stop the current research before opening another conversation.')
      return
    }
    if (threadId !== chat.threadId) chat.openThread(threadId)
    navigate('assistant')
  }

  const runResearch = (request: ResearchRequest) => {
    if (chat.streaming || admitting) return
    const id = crypto.randomUUID()
    setAdmitting(true)
    setNotice(null)
    void chat
      .send(request.prompt, {
        newThread: true,
        onAccepted: (threadId) => {
          workspace.saveDocument({
            id,
            kind: request.kind,
            title: request.title,
            symbols: request.symbols,
            settings: request.settings,
            threadId,
            updatedAt: new Date().toISOString(),
          })
          navigate(request.kind, request.kind === 'companies' ? request.symbols[0] : id)
          setAdmitting(false)
        },
      })
      .finally(() => setAdmitting(false))
  }
  const props: PageProps = {
    target,
    documents: workspace.documents,
    chat,
    personal,
    onRun: runResearch,
    onOpenAssistant: openAssistant,
  }
  const context =
    page === 'companies'
      ? target
      : (workspace.documents.find((doc) => doc.id === target)?.symbols.join(', ') ?? '')

  return (
    <div className="app-shell workspace-shell">
      <a
        className="skip-link"
        href="#workspace-heading"
        onClick={(event) => {
          event.preventDefault()
          document.getElementById('workspace-heading')?.focus()
        }}
      >
        Skip to content
      </a>
      <aside
        className={`workspace-sidebar ${mobileOpen ? 'is-open' : ''}`}
        aria-label="Main navigation"
      >
        <a href="#/overview" className="workspace-brand">
          <span className="brand-mark">
            <Activity size={21} />
          </span>
          <span>
            MiniAlpha<small>THE RESEARCH WORKSPACE</small>
          </span>
        </a>
        <button
          className="icon-button mobile-close"
          aria-label="Close navigation"
          onClick={() => setMobileOpen(false)}
        >
          <X size={20} />
        </button>
        <span className="nav-label">WORKSPACE</span>
        <nav className="workspace-nav">
          {navigation.map(({ page: itemPage, label, Icon }) => (
            <a
              key={itemPage}
              href={`#/${itemPage}`}
              aria-current={page === itemPage ? 'page' : undefined}
            >
              <Icon size={18} />
              <span>{label}</span>
              {page === itemPage && <span className="nav-active-dot" />}
            </a>
          ))}
        </nav>
        <div className="sidebar-following">
          <span className="nav-label">
            FOLLOWING <span>{personal.watchlist.length}</span>
          </span>
          {personal.watchlist.slice(0, 8).map((item) => (
            <a key={item.symbol} href={`#/companies/${encodeURIComponent(item.symbol)}`}>
              <span className="sidebar-ticker-dot" />
              {item.symbol}
              <ArrowUpRight size={12} />
            </a>
          ))}
          {!personal.watchlist.length && <p>Add companies to your watchlist to keep them close.</p>}
        </div>
        <div className="workspace-sidebar-footer">
          <span className="brand-mark small-brand">
            <Activity size={16} />
          </span>
          <div>
            <strong>Your research desk</strong>
            <small>Evidence behind every idea</small>
          </div>
        </div>
      </aside>
      {mobileOpen && (
        <button
          className="nav-scrim"
          aria-label="Close navigation"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <main className="workspace-main">
        <header className="workspace-topbar">
          <button
            className="icon-button mobile-menu"
            aria-label="Open navigation"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen(true)}
          >
            <Menu size={20} />
          </button>
          <div className="breadcrumbs">
            <span>Workspace</span>
            <span>/</span>
            <strong id="workspace-heading" tabIndex={-1}>
              {navigation.find((item) => item.page === page)?.label}
            </strong>
          </div>
          <form
            className="global-search"
            onSubmit={(event) => {
              event.preventDefault()
              const ticker = search.trim().toUpperCase()
              if (SYMBOL_PATTERN.test(ticker)) {
                navigate('companies', ticker)
                setSearch('')
              } else setNotice('Enter a ticker such as AAPL, ^GSPC, or 1155.KL.')
            }}
          >
            <Search size={15} />
            <label className="sr-only" htmlFor="global-ticker">
              Open company by ticker
            </label>
            <input
              id="global-ticker"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Find a ticker…"
              maxLength={20}
              required
            />
          </form>
          {page !== 'assistant' && (
            <button
              className={`assistant-toggle ${assistantOpen ? 'selected' : ''}`}
              aria-expanded={assistantOpen}
              onClick={() => setAssistantOpen((current) => !current)}
            >
              <Bot size={17} />
              <span>Ask assistant</span>
            </button>
          )}
        </header>
        {(workspace.storageError || personal.storageError) && (
          <p className="workspace-notice" role="status">
            {workspace.storageError ?? personal.storageError}
          </p>
        )}
        {notice && (
          <p className="workspace-notice" role="status">
            {notice}
            <button
              className="icon-button"
              aria-label="Dismiss notice"
              onClick={() => setNotice(null)}
            >
              <X size={14} />
            </button>
          </p>
        )}
        {admitting && (
          <p className="workspace-notice" role="status">
            <span className="working-dot" /> Starting your research…
          </p>
        )}
        {!admitting && chat.error && page !== 'assistant' && (
          <p className="workspace-notice error-notice" role="alert">
            {chat.error}
            <button className="text-button" onClick={() => navigate('assistant')}>
              View details
            </button>
          </p>
        )}
        {chat.streaming && page !== 'assistant' && (
          <div className="active-research-bar" role="status">
            <span className="working-dot" /> Research continues as you explore.
            <button className="text-button" onClick={() => navigate('assistant')}>
              View progress
            </button>
            <button className="text-button" onClick={() => void chat.stop()}>
              Stop
            </button>
          </div>
        )}
        <div className="workspace-body">
          {page === 'assistant' ? (
            <div className="assistant-page">
              <aside className="conversation-history">
                <header>
                  <h2>Conversations</h2>
                  <button
                    className="text-button"
                    disabled={chat.streaming}
                    onClick={chat.newThread}
                  >
                    New
                  </button>
                </header>
                {chat.threads.map((thread) => (
                  <button
                    key={thread.thread_id}
                    className={thread.thread_id === chat.threadId ? 'selected' : ''}
                    disabled={chat.streaming}
                    onClick={() => openAssistant(thread.thread_id)}
                  >
                    {thread.title ?? 'Untitled research'}
                    <small>{new Date(thread.updated_at).toLocaleDateString()}</small>
                  </button>
                ))}
                {!chat.threads.length && (
                  <p className="muted">Your conversations will appear here.</p>
                )}
              </aside>
              <ResearchChat chat={chat} />
            </div>
          ) : (
            <div
              className="workspace-content"
              key={page === 'companies' || page === 'journal' ? `${page}:${target}` : page}
            >
              {page === 'overview' && <OverviewPage {...props} />}
              {page === 'companies' && <CompanyPage {...props} />}
              {page === 'compare' && <ComparePage {...props} key={target || 'new'} />}
              {page === 'strategy' && <StrategyPage {...props} key={target || 'new'} />}
              {page === 'journal' && <JournalPage {...props} />}
              <footer className="workspace-footer">
                <Activity size={13} />
                <span>MiniAlpha · Research with perspective</span>
                <span>Data availability varies by provider.</span>
              </footer>
            </div>
          )}
          {assistantOpen && page !== 'assistant' && (
            <aside className="context-assistant">
              <button
                className="context-close icon-button"
                onClick={() => setAssistantOpen(false)}
                aria-label="Close assistant"
              >
                <X size={18} />
              </button>
              <ResearchChat chat={chat} context={context} compact />
            </aside>
          )}
        </div>
      </main>
    </div>
  )
}
