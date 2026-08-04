import type {
  ApiError,
  RunEvent,
  ThreadListResponse,
  ThreadTranscriptResponse,
} from '../types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function errorFrom(response: Response): Promise<Error> {
  let body: ApiError | null = null
  try {
    body = (await response.json()) as ApiError
  } catch {
    // Some proxy and server failures are not JSON.
  }
  const message =
    body?.error?.message ?? body?.detail ?? `${response.status} ${response.statusText}`
  return new Error(message)
}

export async function listThreads(signal?: AbortSignal): Promise<ThreadListResponse> {
  const response = await fetch(`${API_BASE}/api/v1/threads?limit=100`, { signal })
  if (!response.ok) throw await errorFrom(response)
  return (await response.json()) as ThreadListResponse
}

export async function loadTranscript(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadTranscriptResponse> {
  const response = await fetch(`${API_BASE}/api/v1/threads/${threadId}/messages`, {
    signal,
  })
  if (!response.ok) throw await errorFrom(response)
  return (await response.json()) as ThreadTranscriptResponse
}

interface StreamOptions {
  threadId: string | null
  message: string
  requestKey: string
  signal: AbortSignal
  onEvent: (event: RunEvent) => void
}

export async function streamMessage(options: StreamOptions): Promise<void> {
  const path = options.threadId
    ? `/api/v1/threads/${options.threadId}/messages/stream`
    : '/api/v1/threads/messages/stream'
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      messages: [{ role: 'user', content: options.message }],
      request_key: options.requestKey,
    }),
    signal: options.signal,
  })
  if (!response.ok) throw await errorFrom(response)
  if (!response.body) throw new Error('The browser did not expose the response stream.')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) parseFrame(frame, options.onEvent)
    if (done) break
  }
  if (buffer.trim()) parseFrame(buffer, options.onEvent)
}

function parseFrame(frame: string, onEvent: (event: RunEvent) => void): void {
  const data = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')
  if (!data) return
  onEvent(JSON.parse(data) as RunEvent)
}

