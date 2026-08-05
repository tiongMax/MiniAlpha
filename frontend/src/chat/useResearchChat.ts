import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelRun, listThreads, loadTranscript, streamMessage } from '../api/client'
import { queryKeys } from '../lib/queryKeys'
import type { ChatTurn, ThreadTurn } from '../types'
import { reduceRunEvent } from './events'

function transcriptToChat(turns: ThreadTurn[]): ChatTurn[] {
  return turns.map((turn) => ({
    id: turn.run_id,
    turnIndex: turn.turn_index,
    user: turn.message,
    assistant: turn.answer ?? '',
    status: turn.status,
    tools: turn.tool_calls.map((tool, index) => ({
      ...tool,
      tool_call_id: `${turn.run_id}-stored-${index}`,
      status: tool.status ?? (turn.status === 'completed' ? 'ok' : undefined),
    })),
    artifacts: turn.artifacts,
    error: turn.status === 'cancelled' ? undefined : turn.error?.message,
  }))
}

function reconcileTranscript(
  storedTurns: ThreadTurn[],
  liveTurns: ChatTurn[],
): ChatTurn[] {
  return transcriptToChat(storedTurns).map((stored) => {
    if (stored.status !== 'cancelled') return stored
    const live = liveTurns.find((turn) => turn.id === stored.id)
    if (!live) return stored
    return {
      ...stored,
      assistant: live.assistant,
      tools: live.tools,
      artifacts: live.artifacts,
    }
  })
}

export function useResearchChat() {
  const queryClient = useQueryClient()
  const [threadId, setThreadId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)
  const activeStream = useRef<AbortController | null>(null)
  const activeRunId = useRef<string | null>(null)
  const selectedThread = useRef<string | null>(null)
  const turnCache = useRef(new Map<string, ChatTurn[]>())

  const threadListQuery = useQuery({
    queryKey: queryKeys.threads.list(),
    queryFn: ({ signal }) => listThreads(signal),
  })

  const transcriptQuery = useQuery({
    queryKey: queryKeys.threads.detail(threadId ?? '__new__'),
    queryFn: ({ signal }) => loadTranscript(threadId!, signal),
    enabled: threadId !== null && !streaming,
  })

  useEffect(() => {
    selectedThread.current = threadId
  }, [threadId])

  useEffect(() => {
    if (threadId) turnCache.current.set(threadId, turns)
  }, [threadId, turns])

  useEffect(() => {
    const transcript = transcriptQuery.data
    if (transcript && transcript.thread_id === selectedThread.current && !streaming) {
      setTurns((current) => reconcileTranscript(transcript.turns, current))
    }
  }, [streaming, transcriptQuery.data])

  const refreshThreads = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.threads.all }),
    [queryClient],
  )

  const openThread = useCallback((nextThreadId: string) => {
    if (activeStream.current) return
    selectedThread.current = nextThreadId
    setThreadId(nextThreadId)
    setTurns(turnCache.current.get(nextThreadId) ?? [])
    setStreamError(null)
  }, [])

  const newThread = useCallback(() => {
    if (activeStream.current) return
    selectedThread.current = null
    setThreadId(null)
    setTurns([])
    setStreamError(null)
  }, [])

  const send = useCallback(
    async (message: string) => {
      const cleanMessage = message.trim()
      if (!cleanMessage || activeStream.current) return

      const controller = new AbortController()
      activeStream.current = controller
      const optimisticId = crypto.randomUUID()
      const requestKey = crypto.randomUUID()
      const initial: ChatTurn = {
        id: optimisticId,
        user: cleanMessage,
        assistant: '',
        status: 'in_progress',
        tools: [],
        artifacts: [],
      }
      setTurns((current) => [...current, initial])
      setStreaming(true)
      setStreamError(null)

      try {
        await streamMessage({
          threadId: selectedThread.current,
          message: cleanMessage,
          requestKey,
          signal: controller.signal,
          onAccepted: (run) => {
            activeRunId.current = run.run_id
            if (!selectedThread.current) {
              selectedThread.current = run.thread_id
              setThreadId(run.thread_id)
            }
            setTurns((current) =>
              current.map((turn) =>
                turn.id === optimisticId
                  ? { ...turn, id: run.run_id, turnIndex: run.turn_index }
                  : turn,
              ),
            )
          },
          onEvent: (event) => {
            if (!selectedThread.current) {
              selectedThread.current = event.thread_id
              setThreadId(event.thread_id)
            }
            setTurns((current) =>
              current.map((turn) =>
                turn.id === optimisticId || turn.id === event.run_id
                  ? reduceRunEvent(turn, event)
                  : turn,
              ),
            )
          },
        })

        await queryClient.invalidateQueries({ queryKey: queryKeys.threads.list() })
        const completedThreadId = selectedThread.current
        if (completedThreadId) {
          try {
            const transcript = await queryClient.fetchQuery({
              queryKey: queryKeys.threads.detail(completedThreadId),
              queryFn: ({ signal }) => loadTranscript(completedThreadId, signal),
              staleTime: 0,
            })
            if (selectedThread.current === completedThreadId) {
              setTurns((current) => reconcileTranscript(transcript.turns, current))
            }
          } catch {
            await queryClient.invalidateQueries({
              queryKey: queryKeys.threads.detail(completedThreadId),
            })
          }
        }
      } catch (caught) {
        if (controller.signal.aborted) {
          setTurns((current) =>
            current.map((turn) =>
              turn.id === optimisticId || turn.status === 'in_progress'
                ? { ...turn, status: 'error', error: 'The request was interrupted.' }
                : turn,
            ),
          )
        } else {
          const message = caught instanceof Error ? caught.message : 'The request failed.'
          setStreamError(message)
          setTurns((current) =>
            current.map((turn) =>
              turn.id === optimisticId || turn.status === 'in_progress'
                ? { ...turn, status: 'error', error: message }
                : turn,
            ),
          )
        }
      } finally {
        if (activeStream.current === controller) activeStream.current = null
        activeRunId.current = null
        setStreaming(false)
      }
    },
    [queryClient],
  )

  const stop = useCallback(async () => {
    const runId = activeRunId.current
    if (!runId) {
      activeStream.current?.abort()
      return
    }
    try {
      await cancelRun(runId)
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'Cancellation failed.'
      setStreamError(message)
    }
  }, [])

  const queryError = threadListQuery.error ?? transcriptQuery.error
  const error =
    streamError ?? (queryError instanceof Error ? queryError.message : null)

  return {
    threads: threadListQuery.data?.threads ?? [],
    threadId,
    turns,
    loadingThread: threadId !== null && transcriptQuery.isPending && !streaming,
    streaming,
    error,
    refreshThreads,
    openThread,
    newThread,
    send,
    stop,
  }
}
