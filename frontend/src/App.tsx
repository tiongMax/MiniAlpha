import { FormEvent, useEffect, useRef, useState } from 'react'
import {
  Activity,
  Bot,
  ChevronRight,
  Menu,
  MessageSquareText,
  Plus,
  Search,
  Sparkles,
  UserRound,
  Wrench,
  X,
} from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useResearchChat } from './chat/useResearchChat'
import type { Artifact, ChatTurn, ToolCall } from './types'

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(
    new Date(value),
  )
}

function ThreadSidebar({
  mobileOpen,
  onClose,
  chat,
}: {
  mobileOpen: boolean
  onClose: () => void
  chat: ReturnType<typeof useResearchChat>
}) {
  const [query, setQuery] = useState('')
  const visible = chat.threads.filter((thread) =>
    (thread.title ?? 'Untitled research').toLowerCase().includes(query.toLowerCase()),
  )

  return (
    <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`}>
      <div className="brand-row">
        <div className="brand-mark"><Activity size={20} /></div>
        <div><strong>MiniAlpha</strong><span>Research agent</span></div>
        <button className="icon-button mobile-only" onClick={onClose} aria-label="Close menu"><X size={20} /></button>
      </div>
      <button className="new-thread" onClick={() => { chat.newThread(); onClose() }} disabled={chat.streaming}>
        <Plus size={17} /> New research
      </button>
      <label className="search-box">
        <Search size={15} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search threads" />
      </label>
      <div className="thread-label">Recent</div>
      <nav className="thread-list">
        {visible.map((thread) => (
          <button
            key={thread.thread_id}
            className={`thread-item ${chat.threadId === thread.thread_id ? 'active' : ''}`}
            onClick={() => { void chat.openThread(thread.thread_id); onClose() }}
            disabled={chat.streaming}
          >
            <MessageSquareText size={16} />
            <span><strong>{thread.title ?? 'Untitled research'}</strong><small>{formatDate(thread.updated_at)}</small></span>
            <ChevronRight size={14} />
          </button>
        ))}
        {!visible.length && <p className="empty-list">No saved threads yet.</p>}
      </nav>
      <div className="phase-note">
        <span>Phase 7 execution</span>
        Detached runs · server cancellation
      </div>
    </aside>
  )
}

function ToolCard({ tool }: { tool: ToolCall }) {
  return (
    <details className={`tool-card ${tool.status ?? 'running'}`}>
      <summary>
        <span className="tool-icon"><Wrench size={14} /></span>
        <span><strong>{tool.name}</strong><small>{tool.status === 'running' ? 'Running tool' : tool.status === 'error' ? 'Tool returned an error' : 'Tool completed'}</small></span>
        <span className="status-dot" />
      </summary>
      <div className="tool-detail">
        <div><span>Arguments</span><pre>{JSON.stringify(tool.arguments, null, 2)}</pre></div>
        {tool.summary && <div><span>Result</span><p>{tool.summary}</p></div>}
      </div>
    </details>
  )
}

function ArtifactCard({ artifact }: { artifact: Artifact }) {
  return (
    <details className="artifact-card">
      <summary><Sparkles size={14} /> {artifact.artifact_type.replaceAll('_', ' ')}</summary>
      <pre>{JSON.stringify(artifact.data ?? { error: artifact.error }, null, 2)}</pre>
    </details>
  )
}

function Turn({ turn }: { turn: ChatTurn }) {
  return (
    <article className="turn">
      <div className="message user-message">
        <div className="avatar user-avatar"><UserRound size={16} /></div>
        <div><div className="message-label">You</div><p>{turn.user}</p></div>
      </div>
      <div className="message assistant-message">
        <div className="avatar agent-avatar"><Bot size={17} /></div>
        <div className="assistant-content">
          <div className="message-label">MiniAlpha</div>
          {turn.tools.length > 0 && <div className="tool-stack">{turn.tools.map((tool, index) => <ToolCard key={tool.tool_call_id ?? index} tool={tool} />)}</div>}
          {turn.artifacts.length > 0 && <div className="artifact-stack">{turn.artifacts.map((artifact, index) => <ArtifactCard key={`${artifact.artifact_type}-${index}`} artifact={artifact} />)}</div>}
          {turn.assistant ? (
            <div className="markdown"><Markdown remarkPlugins={[remarkGfm]}>{turn.assistant}</Markdown></div>
          ) : turn.status === 'in_progress' ? (
            <div className="thinking"><i /><i /><i /><span>Researching</span></div>
          ) : null}
          {turn.status === 'cancelled' && <div className="inline-notice">Research run cancelled.</div>}
          {turn.error && <div className="inline-error">{turn.error}</div>}
        </div>
      </div>
    </article>
  )
}

function EmptyState({ onPrompt }: { onPrompt: (prompt: string) => void }) {
  const prompts = [
    'Give me a company overview of Apple',
    'Compare Microsoft and Nvidia',
    'What is Tesla’s recent price trend?',
  ]
  return (
    <div className="empty-state">
      <div className="hero-mark"><Activity size={30} /></div>
      <h1>Financial research, made inspectable.</h1>
      <p>Ask a question and watch MiniAlpha reason through live model output, tool calls, and structured evidence.</p>
      <div className="prompt-grid">
        {prompts.map((prompt) => <button key={prompt} onClick={() => onPrompt(prompt)}>{prompt}<ChevronRight size={15} /></button>)}
      </div>
    </div>
  )
}

export default function App() {
  const chat = useResearchChat()
  const [input, setInput] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: chat.streaming ? 'smooth' : 'auto' })
  }, [chat.turns, chat.streaming])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const message = input.trim()
    if (!message || chat.streaming) return
    setInput('')
    void chat.send(message)
  }

  return (
    <div className="app-shell">
      <ThreadSidebar mobileOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} chat={chat} />
      {sidebarOpen && <button className="sidebar-scrim" onClick={() => setSidebarOpen(false)} aria-label="Close menu" />}
      <main className="main-panel">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} aria-label="Open menu"><Menu size={20} /></button>
          <div><strong>{chat.threadId ? 'Research thread' : 'New research'}</strong><span><i className="online-dot" /> API via live SSE</span></div>
          {chat.threadId && <button className="topbar-new" onClick={chat.newThread} disabled={chat.streaming}><Plus size={15} /> New</button>}
        </header>
        <section className="conversation">
          <div className="conversation-inner">
            {chat.loadingThread ? <div className="page-loader"><Activity className="spin" /> Loading conversation</div> : chat.turns.length ? chat.turns.map((turn) => <Turn key={turn.id} turn={turn} />) : <EmptyState onPrompt={setInput} />}
            {chat.error && <div className="global-error">{chat.error}</div>}
            <div ref={bottomRef} />
          </div>
        </section>
        <footer className="composer-wrap">
          <form className="composer" onSubmit={submit}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
              placeholder="Ask about a company, ticker, or comparison…"
              rows={1}
              disabled={chat.streaming}
            />
            {chat.streaming ? (
              <button type="button" className="running-button" onClick={() => void chat.stop()}><X size={17} /> Stop</button>
            ) : (
              <button type="submit" className="send-button" disabled={!input.trim()}><Sparkles size={17} /> Ask</button>
            )}
          </form>
          <p>MiniAlpha can make mistakes. Verify important financial decisions.</p>
        </footer>
      </main>
    </div>
  )
}
