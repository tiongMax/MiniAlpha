import type {
  ApiError,
  RunAcceptedResponse,
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
  onAccepted: (run: RunAcceptedResponse) => void
}

export async function streamMessage(options: StreamOptions): Promise<void> {
  const path = options.threadId
    ? `/api/v1/threads/${options.threadId}/runs`
    : '/api/v1/threads/runs'
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      messages: [{ role: 'user', content: options.message }],
      request_key: options.requestKey,
    }),
    signal: options.signal,
  })
  if (!response.ok) throw await errorFrom(response)
  const accepted = (await response.json()) as RunAcceptedResponse
  options.onAccepted(accepted)

  const eventsResponse = await fetch(`${API_BASE}${accepted.events_url}`, {
    headers: { Accept: 'text/event-stream' },
    signal: options.signal,
  })
  if (!eventsResponse.ok) throw await errorFrom(eventsResponse)
  if (!eventsResponse.body) {
    throw new Error('The browser did not expose the response stream.')
  }

  const reader = eventsResponse.body.getReader()
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

export async function cancelRun(runId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/runs/${runId}/cancel`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw await errorFrom(response)
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

