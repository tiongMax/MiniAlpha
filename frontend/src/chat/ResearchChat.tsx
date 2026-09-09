import { useEffect, useRef, useState } from 'react'
import { Bot, Plus, Send, Square, Wrench } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArtifactStack } from '../artifacts/ArtifactRenderer'
import type { useResearchChat } from './useResearchChat'
import { navigate } from '../workspace/model'

export type ResearchChatController = ReturnType<typeof useResearchChat>

export function ResearchChat({
  chat,
  context = '',
  compact = false,
}: {
  chat: ResearchChatController
  context?: string
  compact?: boolean
}) {
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  useEffect(() => {
    if (followRef.current && scrollRef.current)
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [chat.turns])

  return (
    <section
      className={`research-chat ${compact ? 'compact-chat' : ''}`}
      aria-label="Research assistant"
    >
      <div className="assistant-heading">
        <Bot size={19} />
        <div>
          <strong>Research assistant</strong>
          <small>{context ? `Context: ${context}` : 'Explore a question in depth'}</small>
        </div>
        <button
          className="icon-button"
          aria-label="New conversation"
          disabled={chat.streaming}
          onClick={chat.newThread}
        >
          <Plus size={18} />
        </button>
      </div>
      <div
        className="chat-scroll"
        ref={scrollRef}
        onScroll={() => {
          const element = scrollRef.current
          if (element)
            followRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80
        }}
      >
        {chat.loadingThread ? (
          <p role="status">Loading conversation…</p>
        ) : chat.turns.length === 0 ? (
          <div className="assistant-empty">
            <Bot size={30} />
            <h2>A second perspective.</h2>
            <p>Ask about the evidence, challenge a thesis, or explore an idea.</p>
            {['What are the main risks?', 'Summarize the valuation.'].map((prompt) => (
              <button className="secondary-button" key={prompt} onClick={() => setInput(prompt)}>
                {prompt}
              </button>
            ))}
          </div>
        ) : (
          chat.turns.map((turn) => (
            <article className="chat-turn" key={turn.id}>
              <p className="chat-question">{turn.user}</p>
              {turn.status === 'in_progress' && (
                <p className="run-status" role="status">
                  <span className="working-dot" />
                  {turn.progress?.message ?? 'Researching…'}
                </p>
              )}
              {!!turn.tools.length && (
                <details className="tool-summary">
                  <summary>
                    <Wrench size={13} /> {turn.tools.length} research steps
                  </summary>
                  {turn.tools.map((tool, index) => (
                    <div key={tool.tool_call_id ?? index}>
                      <strong>{tool.name.replaceAll('_', ' ')}</strong>
                      <span>{tool.status ?? 'running'}</span>
                      {tool.summary && <p>{tool.summary}</p>}
                    </div>
                  ))}
                </details>
              )}
              <ArtifactStack
                artifacts={turn.artifacts}
                onOpenCompany={(symbol) => navigate('companies', symbol)}
              />
              <div className="markdown">
                <Markdown remarkPlugins={[remarkGfm]}>{turn.assistant}</Markdown>
              </div>
              {turn.status === 'cancelled' && (
                <p className="inline-notice">
                  Research cancelled. Any partial results are shown above.
                </p>
              )}
              {turn.error && (
                <p className="inline-error" role="alert">
                  {turn.error}
                </p>
              )}
            </article>
          ))
        )}
        {chat.error && (
          <p className="inline-error" role="alert">
            {chat.error}
          </p>
        )}
      </div>
      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault()
          if (!input.trim() || chat.streaming) return
          followRef.current = true
          void chat.send(context ? `Regarding ${context}: ${input.trim()}` : input.trim())
          setInput('')
        }}
      >
        <label className="sr-only" htmlFor={compact ? 'context-message' : 'assistant-message'}>
          Research question
        </label>
        <textarea
          id={compact ? 'context-message' : 'assistant-message'}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={context ? `Ask about ${context}…` : 'Ask a research question…'}
          rows={2}
          maxLength={9000}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
        />
        {chat.streaming ? (
          <button className="primary-button" type="button" onClick={() => void chat.stop()}>
            <Square size={14} /> Stop
          </button>
        ) : (
          <button className="primary-button" disabled={!input.trim()}>
            <Send size={15} /> Ask
          </button>
        )}
      </form>
    </section>
  )
}
