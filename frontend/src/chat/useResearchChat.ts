import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listThreads, loadTranscript, streamMessage } from '../api/client'
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
      status: turn.status === 'completed' ? 'ok' : undefined,
    })),
    artifacts: turn.artifacts,
    error: turn.error?.message,
  }))
}

export function useResearchChat() {
  const queryClient = useQueryClient()
  const [threadId, setThreadId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [streaming, setStreaming] = useState(false)
  const [streamError, setStreamError] = useState<string | null>(null)
  const activeStream = useRef<AbortController | null>(null)
  const selectedThread = useRef<string | null>(null)

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
    const transcript = transcriptQuery.data
    if (transcript && transcript.thread_id === selectedThread.current && !streaming) {
      setTurns(transcriptToChat(transcript.turns))
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
    setTurns([])
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
              setTurns(transcriptToChat(transcript.turns))
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
                ? { ...turn, status: 'stopped' }
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
        setStreaming(false)
      }
    },
    [queryClient],
  )

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
  }
}
