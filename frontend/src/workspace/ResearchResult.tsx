import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Square } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { loadTranscript } from '../api/client'
import { queryKeys } from '../lib/queryKeys'
import { ArtifactStack } from '../artifacts/ArtifactRenderer'
import type { ResearchChatController } from '../chat/ResearchChat'
import type { Artifact } from '../types'
import { artifactSymbols, navigate, type ResearchDocument } from './model'

export function ResearchResult({
  document,
  chat,
  symbol,
  filter,
  onOpenAssistant,
}: {
  document?: ResearchDocument
  chat: ResearchChatController
  symbol?: string
  filter?: (artifact: Artifact) => boolean
  onOpenAssistant: (threadId: string) => void
}) {
  const live = !!document && chat.threadId === document.threadId
  const transcript = useQuery({
    queryKey: queryKeys.threads.detail(document?.threadId ?? '__workspace__'),
    queryFn: ({ signal }) => loadTranscript(document!.threadId, signal),
    enabled: !!document && !live,
    refetchInterval: (query) =>
      query.state.data?.turns.at(-1)?.status === 'in_progress' ? 3000 : false,
  })
  if (!document)
    return (
      <div className="workspace-empty">
        <span className="empty-cross">+</span>
        <h3>Your evidence belongs here.</h3>
        <p>Run an analysis to build a report with charts, source data, and an explanation.</p>
      </div>
    )
  if (!live && transcript.isPending)
    return (
      <div className="workspace-empty" role="status">
        Loading saved research…
      </div>
    )
  if (!live && transcript.error)
    return (
      <div className="inline-error" role="alert">
        <p>{transcript.error.message}</p>
        <button className="secondary-button" onClick={() => void transcript.refetch()}>
          Retry loading
        </button>
      </div>
    )
  const turns = live
    ? chat.turns
    : (transcript.data?.turns.map((turn) => ({
        ...turn,
        assistant: turn.answer ?? '',
        error: turn.error?.message,
      })) ?? [])
  const latest = turns.at(-1)
  const artifacts = turns
    .flatMap((turn) => turn.artifacts)
    .filter(
      (artifact) =>
        (!symbol || artifactSymbols(artifact).includes(symbol)) && (!filter || filter(artifact)),
    )
  // Keep the newest result for each dataset and entity across follow-up turns.
  const unique = [
    ...new Map(
      artifacts.map((artifact) => [
        `${artifact.artifact_type}:${artifactSymbols(artifact).join(',')}`,
        artifact,
      ]),
    ).values(),
  ]
  const inProgress = latest?.status === 'in_progress'
  return (
    <section className="research-result" aria-label="Analysis results">
      <div className="result-heading">
        <div>
          <span className="eyebrow">RESEARCH REPORT</span>
          <h3>{document.title}</h3>
        </div>
        <button
          className="text-button"
          disabled={chat.streaming && !live}
          onClick={() => onOpenAssistant(document.threadId)}
        >
          Open conversation <ExternalLink size={13} />
        </button>
      </div>
      {inProgress && (
        <div className="run-banner" role="status">
          <span className="working-dot" /> Research in progress. Results appear as they arrive.
          {live && (
            <button className="text-button" onClick={() => void chat.stop()}>
              <Square size={12} /> Stop
            </button>
          )}
        </div>
      )}
      {latest?.status === 'cancelled' && (
        <p className="inline-notice">This run was cancelled. Results may be incomplete.</p>
      )}
      {latest?.error && (
        <p className="inline-error" role="alert">
          {latest.error}
        </p>
      )}
      <ArtifactStack artifacts={unique} onOpenCompany={(ticker) => navigate('companies', ticker)} />
      {!unique.length && !inProgress && (
        <p className="muted">
          No structured data is available for this view. Check the research explanation or try
          another analysis.
        </p>
      )}
      {latest?.assistant && (
        <details className="research-explanation" open={!filter}>
          <summary>Research explanation</summary>
          <div className="markdown">
            <Markdown remarkPlugins={[remarkGfm]}>{latest.assistant}</Markdown>
          </div>
        </details>
      )}
    </section>
  )
}
