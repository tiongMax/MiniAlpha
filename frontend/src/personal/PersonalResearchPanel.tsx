import { FormEvent, useEffect, useState } from 'react'
import { ArrowUpRight, BookOpen, Plus, Trash2, X } from 'lucide-react'
import { usePersonalResearch } from './usePersonalResearch'

interface PersonalResearchPanelProps {
  open: boolean
  onClose: () => void
  onResearch: (prompt: string) => void
}

function entryDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function PersonalResearchPanel({ open, onClose, onResearch }: PersonalResearchPanelProps) {
  const research = usePersonalResearch()
  const [symbol, setSymbol] = useState('')
  const [selectedSymbol, setSelectedSymbol] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!research.watchlist.length) {
      setSelectedSymbol('')
    } else if (!research.watchlist.some((item) => item.symbol === selectedSymbol)) {
      setSelectedSymbol(research.watchlist[0].symbol)
    }
  }, [research.watchlist, selectedSymbol])

  if (!open) return null

  const addTicker = (event: FormEvent) => {
    event.preventDefault()
    const nextError = research.addSymbol(symbol)
    setError(nextError)
    if (!nextError) {
      setSelectedSymbol(symbol.trim().toUpperCase())
      setSymbol('')
    }
  }

  const saveNote = (event: FormEvent) => {
    event.preventDefault()
    const nextError = research.addJournalEntry(selectedSymbol, note)
    setError(nextError)
    if (!nextError) setNote('')
  }

  const visibleEntries = research.journal.filter(
    (entry) => !selectedSymbol || entry.symbol === selectedSymbol,
  )

  return (
    <>
      <button className="personal-scrim" onClick={onClose} aria-label="Close personal research" />
      <aside className="personal-panel" aria-label="Watchlist and research journal">
        <header>
          <div className="personal-title">
            <BookOpen size={18} />
            <div>
              <strong>My research</strong>
              <span>Saved on this device</span>
            </div>
          </div>
          <button className="panel-close" onClick={onClose} aria-label="Close">
            <X size={19} />
          </button>
        </header>

        <section className="personal-section">
          <div className="section-heading">
            <strong>Watchlist</strong>
            <span>{research.watchlist.length} saved</span>
          </div>
          <form className="ticker-form" onSubmit={addTicker}>
            <input
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              placeholder="AAPL or ^GSPC"
              aria-label="Ticker symbol"
            />
            <button type="submit">
              <Plus size={15} /> Add
            </button>
          </form>
          <div className="watchlist-items">
            {research.watchlist.map((item) => (
              <div
                className={`watchlist-item ${selectedSymbol === item.symbol ? 'selected' : ''}`}
                key={item.symbol}
              >
                <button className="watchlist-select" onClick={() => setSelectedSymbol(item.symbol)}>
                  {item.symbol}
                </button>
                <button
                  className="research-ticker"
                  onClick={() => {
                    onResearch(
                      `Give me a current overview and price-risk analysis for ${item.symbol}.`,
                    )
                    onClose()
                  }}
                  aria-label={`Research ${item.symbol}`}
                >
                  <ArrowUpRight size={14} />
                </button>
                <button
                  className="delete-item"
                  onClick={() => research.removeSymbol(item.symbol)}
                  aria-label={`Remove ${item.symbol}`}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            {!research.watchlist.length && (
              <p className="personal-empty">Add the stocks and indices you regularly follow.</p>
            )}
          </div>
        </section>

        <section className="personal-section journal-section">
          <div className="section-heading">
            <strong>Research journal</strong>
            <span>{selectedSymbol || 'No ticker selected'}</span>
          </div>
          <form className="journal-form" onSubmit={saveNote}>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Write your thesis, risks, or what would change your mind…"
              rows={4}
              disabled={!selectedSymbol}
            />
            <button type="submit" disabled={!selectedSymbol || !note.trim()}>
              Save note
            </button>
          </form>
          {error && <p className="personal-error">{error}</p>}
          <div className="journal-entries">
            {visibleEntries.map((entry) => (
              <article key={entry.id}>
                <header>
                  <strong>{entry.symbol}</strong>
                  <button
                    onClick={() => research.removeJournalEntry(entry.id)}
                    aria-label="Delete note"
                  >
                    <Trash2 size={12} />
                  </button>
                </header>
                <p>{entry.note}</p>
                <time>{entryDate(entry.createdAt)}</time>
              </article>
            ))}
            {!visibleEntries.length && selectedSymbol && (
              <p className="personal-empty">No notes for {selectedSymbol} yet.</p>
            )}
          </div>
        </section>
      </aside>
    </>
  )
}
