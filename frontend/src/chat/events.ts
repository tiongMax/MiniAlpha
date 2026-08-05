import type { Artifact, ChatTurn, RunEvent, ToolCall } from '../types'

function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

export function reduceRunEvent(turn: ChatTurn, event: RunEvent): ChatTurn {
  const data = event.data
  switch (event.event) {
    case 'metadata':
      return {
        ...turn,
        id: event.run_id,
        turnIndex: typeof data.turn_index === 'number' ? data.turn_index : turn.turnIndex,
        progress: {
          phase: 'admitted',
          message: 'Run admitted. Waiting for the research worker…',
          startedAt: event.timestamp,
        },
      }
    case 'progress':
      return {
        ...turn,
        progress: {
          phase: text(data.phase) ?? 'working',
          message: text(data.message) ?? 'Research is in progress…',
          startedAt: turn.progress?.startedAt ?? event.timestamp,
        },
      }
    case 'message_chunk':
      return { ...turn, assistant: turn.assistant + (text(data.delta) ?? '') }
    case 'tool_call': {
      const call: ToolCall = {
        tool_call_id: text(data.tool_call_id),
        name: text(data.name) ?? 'unknown tool',
        arguments: record(data.arguments),
        status: 'running',
      }
      return { ...turn, tools: [...turn.tools, call] }
    }
    case 'tool_result': {
      const callId = text(data.tool_call_id)
      const status: ToolCall['status'] = data.status === 'error' ? 'error' : 'ok'
      const nextTools = turn.tools.map((tool) =>
        tool.tool_call_id === callId
          ? { ...tool, status, summary: text(data.summary) }
          : tool,
      )
      return { ...turn, tools: nextTools }
    }
    case 'artifact': {
      const artifact = data as unknown as Artifact
      const artifacts = artifact.status === 'ok'
        ? turn.artifacts.filter(
            (existing) =>
              existing.artifact_type !== artifact.artifact_type || existing.status !== 'error',
          )
        : turn.artifacts
      return { ...turn, artifacts: [...artifacts, artifact] }
    }
    case 'error':
      return {
        ...turn,
        status: 'error',
        progress: undefined,
        error: text(data.message) ?? 'The run failed.',
      }
    case 'run_end':
      return {
        ...turn,
        progress: undefined,
        status:
          data.status === 'completed'
            ? 'completed'
            : data.status === 'cancelled'
              ? 'cancelled'
              : 'error',
      }
  }
}
