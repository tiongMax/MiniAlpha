import type {
  ApiError,
  RunAcceptedResponse,
  RunEvent,
  ThreadListResponse,
  ThreadTranscriptResponse,
} from '../types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

class HttpResponseError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'HttpResponseError'
  }
}

async function errorFrom(response: Response): Promise<HttpResponseError> {
  let body: ApiError | null = null
  try {
    body = (await response.json()) as ApiError
  } catch {
    // Some proxy and server failures are not JSON.
  }
  const message =
    body?.error?.message ?? body?.detail ?? `${response.status} ${response.statusText}`
  return new HttpResponseError(message, response.status)
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

  let lastEventId = 0
  let terminal = false
  let reconnectDelay = 250
  while (!terminal) {
    try {
      const result = await attachEvents(
        accepted.events_url,
        lastEventId,
        options.signal,
        options.onEvent,
      )
      lastEventId = result.lastEventId
      terminal = result.terminal
      reconnectDelay = 250
      if (!terminal) await abortableDelay(reconnectDelay, options.signal)
    } catch (caught) {
      if (options.signal.aborted) throw caught
      if (
        caught instanceof HttpResponseError &&
        caught.status < 500 &&
        caught.status !== 429
      ) {
        throw caught
      }
      await abortableDelay(reconnectDelay, options.signal)
      reconnectDelay = Math.min(reconnectDelay * 2, 5_000)
    }
  }
}

export async function cancelRun(runId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/runs/${runId}/cancel`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw await errorFrom(response)
}

async function attachEvents(
  eventsUrl: string,
  lastEventId: number,
  signal: AbortSignal,
  onEvent: (event: RunEvent) => void,
): Promise<{ lastEventId: number; terminal: boolean }> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (lastEventId > 0) headers['Last-Event-ID'] = String(lastEventId)
  const response = await fetch(`${API_BASE}${eventsUrl}`, { headers, signal })
  if (!response.ok) throw await errorFrom(response)
  if (!response.body) throw new Error('The browser did not expose the response stream.')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let cursor = lastEventId
  let terminal = false
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const event = parseFrame(frame)
      if (!event) continue
      cursor = Math.max(cursor, event.event_id)
      terminal ||= event.event === 'run_end'
      onEvent(event)
    }
    if (done || terminal) break
  }
  if (buffer.trim() && !terminal) {
    const event = parseFrame(buffer)
    if (event) {
      cursor = Math.max(cursor, event.event_id)
      terminal = event.event === 'run_end'
      onEvent(event)
    }
  }
  return { lastEventId: cursor, terminal }
}

function parseFrame(frame: string): RunEvent | null {
  const data = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')
  if (!data) return null
  return JSON.parse(data) as RunEvent
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeout)
      reject(new DOMException('The request was aborted.', 'AbortError'))
    }
    const timeout = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, milliseconds)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

