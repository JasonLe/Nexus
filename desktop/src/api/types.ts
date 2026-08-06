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
  /** 来源：内置工具或 MCP 服务器（旧后端可能缺失，按 mcp__ 前缀兜底） */
  origin?: 'builtin' | 'mcp'
  /** MCP 工具所属服务器名 */
  server?: string | null
}

// ---- MCP 服务器契约 ----

export type McpTransport = 'stdio' | 'http'

export interface McpServerTool {
  name: string
  description: string
}

/** GET /api/mcp 返回项：配置字段（env 为掩码）+ 运行时状态 */
export interface McpServerDto {
  name: string
  command: string | null
  args: string[]
  /** env 值已掩码（前3后4），仅用于回显，提交未修改项会被后端跳过 */
  env: Record<string, string> | null
  url: string | null
  enabled: boolean
  transport: McpTransport
  status: string
  error: string | null
  tool_count: number
  tools: McpServerTool[]
}

/** POST /api/mcp 与 PUT /api/mcp/{name} 的请求体 */
export interface McpServerInput {
  name: string
  command?: string | null
  args?: string[]
  env?: Record<string, string>
  url?: string | null
  enabled?: boolean
}

/** POST /api/mcp/test 返回：连接测试结果（不持久化） */
export interface McpTestResult {
  ok: boolean
  tools: string[]
  error: string | null
}

// ---- WebSocket 消息契约类型 ----

export type WsClientMessage =
  | { type: 'message'; content: string }
  | { type: 'reset' }
  | { type: 'restore'; messages: HistoryMessage[]; session_id?: string }
  | { type: 'slash_command'; command: string }

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
  | { type: 'done'; steps: number; usage: UsageInfo; session_id?: string }
  | { type: 'error'; message: string }
  | { type: 'reset_ok' }
  | { type: 'restore_ok'; message_count: number }
  | { type: 'slash_command_result'; command: string; title: string; content: unknown }
