export type RunStatus = 'in_progress' | 'completed' | 'error' | 'cancelled'

export interface ApiError {
  error?: { code?: string; message?: string }
  detail?: string
}

export interface ThreadSummary {
  thread_id: string
  status: RunStatus
  title: string | null
  created_at: string
  updated_at: string
}

export interface ThreadListResponse {
  threads: ThreadSummary[]
  total: number
  limit: number
  offset: number
}

export interface ToolCall {
  tool_call_id?: string
  name: string
  arguments: Record<string, unknown>
  status?: 'running' | 'ok' | 'error'
  summary?: string
}

export interface Artifact {
  artifact_type: string
  schema_version: number
  status: 'ok' | 'error'
  data?: Record<string, unknown> | null
  error?: string | null
}

export interface ThreadTurn {
  run_id: string
  turn_index: number
  attempt_no: number
  status: RunStatus
  message: string
  answer: string | null
  tool_calls: ToolCall[]
  artifacts: Artifact[]
  error: { code: string; message: string } | null
  started_at: string
  completed_at: string | null
}

export interface ThreadTranscriptResponse {
  thread_id: string
  turns: ThreadTurn[]
}

export type RunEventName =
  | 'metadata'
  | 'message_chunk'
  | 'tool_call'
  | 'tool_result'
  | 'artifact'
  | 'error'
  | 'run_end'

export interface RunEvent {
  event_id: number
  event: RunEventName
  run_id: string
  thread_id: string
  timestamp: string
  data: Record<string, unknown>
}

export interface RunAcceptedResponse {
  run_id: string
  thread_id: string
  turn_index: number
  status: RunStatus
  replayed: boolean
  events_url: string
}

export interface ChatTurn {
  id: string
  turnIndex?: number
  user: string
  assistant: string
  status: RunStatus
  tools: ToolCall[]
  artifacts: Artifact[]
  error?: string
}

