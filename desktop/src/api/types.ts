// ---- REST API 契约类型 ----

export interface HealthInfo {
  ok: boolean
  provider: string
  model: string
}

export interface ProviderConfig {
  api_key: string | null
  has_api_key: boolean
  model: string
  max_tokens: number
  context_window_tokens: number
  base_url: string | null
}

export interface NexusConfigDto {
  providers: Record<string, ProviderConfig>
  default_provider: string
  agent: {
    system_prompt: string
    max_steps: number
  }
  tools: {
    enabled: string[]
  }
  stream: boolean
}

export interface SessionSummary {
  id: string
  timestamp: string | number
  summary: string
  message_count: number
  log_file?: string
}

export interface HistoryMessage {
  role: string
  content: string
  [key: string]: unknown
}

export interface SessionDetail extends Partial<SessionSummary> {
  messages: HistoryMessage[]
}

export interface ToolSchemaProperty {
  type?: string
  description?: string
  enum?: unknown[]
  default?: unknown
  items?: { type?: string }
}

export interface ToolSchema {
  type?: string
  properties?: Record<string, ToolSchemaProperty>
  required?: string[]
}

export interface ToolInfo {
  name: string
  description: string
  schema: ToolSchema
}

// ---- WebSocket 消息契约类型 ----

export type WsClientMessage =
  | { type: 'message'; content: string }
  | { type: 'reset' }
  | { type: 'restore'; messages: HistoryMessage[] }

export interface UsageInfo {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export type WsServerMessage =
  | { type: 'thinking_delta'; delta: string }
  | { type: 'content_delta'; delta: string }
  | {
      type: 'tool_call'
      name: string
      args: Record<string, unknown>
      result: string
      error: string | null
      success: boolean
      index: number
    }
  | ({ type: 'usage' } & UsageInfo)
  | { type: 'done'; steps: number; usage: UsageInfo }
  | { type: 'error'; message: string }
  | { type: 'reset_ok' }
  | { type: 'restore_ok'; message_count: number }
