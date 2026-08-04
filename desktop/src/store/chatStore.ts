import { create } from 'zustand'
import { api } from '../api/client'
import type { HistoryMessage, SessionSummary, UsageInfo, WsServerMessage } from '../api/types'
import { ChatSocket, type WsStatus } from '../api/ws'
import { toast } from './toastStore'

export interface ToolCallRecord {
  index: number
  name: string
  args: Record<string, unknown>
  result: string
  error: string | null
  success: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  thinking: string
  toolCalls: ToolCallRecord[]
  usage: UsageInfo | null
  steps: number | null
  streaming: boolean
  error: string | null
  /** 来自历史会话恢复的只读消息 */
  historical: boolean
}

export type LogEntryType = 'llm' | 'tool' | 'event' | 'error'

export interface LogEntry {
  id: string
  timestamp: number
  type: LogEntryType
  label: string
  content: string
}

interface ChatState {
  messages: ChatMessage[]
  wsStatus: WsStatus
  running: boolean
  sessions: SessionSummary[]
  sessionsLoading: boolean
  activeSessionId: string | null
  logs: LogEntry[]
  clearLogs: () => void
  init: () => void
  send: (content: string) => void
  sendSlashCommand: (command: string) => void
  newSession: () => void
  loadSessions: () => Promise<void>
  restoreSession: (id: string) => Promise<void>
  deleteSession: (id: string) => Promise<void>
}

let socket: ChatSocket | null = null
let msgSeq = 0

function nextMsgId(): string {
  msgSeq += 1
  return `m-${Date.now()}-${msgSeq}`
}

function emptyAssistant(): ChatMessage {
  return {
    id: nextMsgId(),
    role: 'assistant',
    content: '',
    thinking: '',
    toolCalls: [],
    usage: null,
    steps: null,
    streaming: true,
    error: null,
    historical: false,
  }
}

export const useChatStore = create<ChatState>((set, get) => {
  function patchAssistant(id: string, patch: Partial<ChatMessage>): void {
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    }))
  }

  function currentAssistant(): ChatMessage | undefined {
    const msgs = get().messages
    return msgs.length > 0 ? msgs[msgs.length - 1] : undefined
  }

  let logSeq = 0
  function addLog(type: LogEntryType, label: string, content: string): void {
    logSeq += 1
    const entry: LogEntry = {
      id: `log-${Date.now()}-${logSeq}`,
      timestamp: Date.now(),
      type,
      label,
      content,
    }
    set((s) => ({ logs: [...s.logs, entry] }))
  }

  function handleWsMessage(msg: WsServerMessage): void {
    const last = currentAssistant()
    switch (msg.type) {
      case 'thinking_delta':
        addLog('llm', 'thinking', msg.delta)
        if (last && last.role === 'assistant' && last.streaming) {
          patchAssistant(last.id, { thinking: last.thinking + msg.delta })
        }
        break
      case 'content_delta':
        addLog('llm', 'content', msg.delta)
        if (last && last.role === 'assistant' && last.streaming) {
          patchAssistant(last.id, { content: last.content + msg.delta })
        }
        break
      case 'tool_call':
        addLog('tool', msg.name, JSON.stringify(msg.args))
        if (last && last.role === 'assistant' && last.streaming) {
          patchAssistant(last.id, {
            toolCalls: [
              ...last.toolCalls,
              {
                index: msg.index,
                name: msg.name,
                args: msg.args,
                result: msg.result,
                error: msg.error,
                success: msg.success,
              },
            ],
          })
        }
        break
      case 'usage':
        addLog('event', 'usage', `${msg.total_tokens} tokens`)
        if (last && last.role === 'assistant' && last.streaming) {
          patchAssistant(last.id, {
            usage: {
              prompt_tokens: msg.prompt_tokens,
              completion_tokens: msg.completion_tokens,
              total_tokens: msg.total_tokens,
            },
          })
        }
        break
      case 'done':
        addLog('event', 'done', `steps=${msg.steps}, tokens=${msg.usage?.total_tokens ?? 0}`)
        if (last && last.role === 'assistant' && last.streaming) {
          patchAssistant(last.id, {
            streaming: false,
            steps: msg.steps,
            usage: msg.usage,
          })
        }
        set({ running: false })
        // 刷新会话列表，使新会话自动出现在左侧
        void get().loadSessions()
        // 若后端返回了 session_id，标记为当前活跃会话
        if ('session_id' in msg && msg.session_id) {
          set({ activeSessionId: msg.session_id })
        }
        break
      case 'error':
        addLog('error', 'error', msg.message)
        if (last && last.role === 'assistant' && last.streaming) {
          patchAssistant(last.id, { streaming: false, error: msg.message })
        } else {
          set((s) => ({
            messages: [
              ...s.messages,
              { ...emptyAssistant(), streaming: false, error: msg.message },
            ],
          }))
        }
        set({ running: false })
        break
      case 'reset_ok':
      case 'restore_ok':
        break
      case 'slash_command_result':
        addLog('event', msg.command, msg.title)
        // 添加一条系统消息显示命令结果
        set((s) => ({
          messages: [
            ...s.messages,
            {
              id: nextMsgId(),
              role: 'assistant' as const,
              content: `**${msg.title}**\n\n${
                Array.isArray(msg.content)
                  ? msg.content.map((item: any) => {
                      if (item.name && item.description) {
                        return `- **${item.name}**: ${item.description}`
                      }
                      if (item.summary) {
                        return `- ${item.summary} (${item.message_count} 条消息)`
                      }
                      return String(item)
                    }).join('\n')
                  : String(msg.content)
              }`,
              thinking: '',
              toolCalls: [],
              usage: null,
              steps: null,
              streaming: false,
              error: null,
              historical: false,
            },
          ],
        }))
        break
    }
  }

  function buildHistoryForRestore(): HistoryMessage[] {
    // 用当前渲染的 user/assistant 内容重建上下文（WS 每连接独立历史，重连需恢复）
    return get()
      .messages.filter((m) => !m.error && m.content.trim().length > 0)
      .map((m) => ({ role: m.role, content: m.content }))
  }

 return {
    messages: [],
    wsStatus: 'connecting',
    running: false,
    sessions: [],
    sessionsLoading: false,
    activeSessionId: null,
    logs: [],

    clearLogs: () => {
      logSeq = 0
      set({ logs: [] })
    },

    init: () => {
      if (socket) return
      socket = new ChatSocket({
        onMessage: handleWsMessage,
        onStatus: (wsStatus) => set({ wsStatus }),
        onReconnected: buildHistoryForRestore,
      })
      socket.connect()
      void get().loadSessions()
    },

    send: (content: string) => {
      const text = content.trim()
      if (!text) return
      if (get().running) return
      if (!socket || !socket.ready) {
        toast('error', '连接尚未建立，请稍候重试')
        return
      }
      const userMsg: ChatMessage = {
        id: nextMsgId(),
        role: 'user',
        content: text,
        thinking: '',
        toolCalls: [],
        usage: null,
        steps: null,
        streaming: false,
        error: null,
        historical: false,
      }
      set((s) => ({
        messages: [...s.messages, userMsg, emptyAssistant()],
        running: true,
        activeSessionId: null,
      }))
      const ok = socket.send({ type: 'message', content: text })
      if (!ok) {
        set({ running: false })
        toast('error', '消息发送失败：连接已断开')
      }
    },

    sendSlashCommand: (command: string) => {
      if (!socket || !socket.ready) {
        toast('error', '连接尚未建立，请稍候重试')
        return
      }
      const userMsg: ChatMessage = {
        id: nextMsgId(),
        role: 'user',
        content: command,
        thinking: '',
        toolCalls: [],
        usage: null,
        steps: null,
        streaming: false,
        error: null,
        historical: false,
      }
      set((s) => ({
        messages: [...s.messages, userMsg],
      }))
      addLog('event', 'command', command)
      const ok = socket.send({ type: 'slash_command', command })
      if (!ok) {
        toast('error', '命令发送失败：连接已断开')
      }
    },

    newSession: () => {
      socket?.send({ type: 'reset' })
      logSeq = 0
      set({ messages: [], running: false, activeSessionId: null, logs: [] })
    },

    loadSessions: async () => {
      set({ sessionsLoading: true })
      try {
        const sessions = await api.listSessions()
        set({ sessions })
      } catch {
        set({ sessions: [] })
      } finally {
        set({ sessionsLoading: false })
      }
    },

    restoreSession: async (id: string) => {
      if (get().running) {
        toast('info', '任务执行中，无法切换会话')
        return
      }
      try {
        const detail = await api.getSession(id)
        const history = (detail.messages ?? []).filter(
          (m) => m.role === 'user' || m.role === 'assistant',
        )
        const restored: ChatMessage[] = history.map((m) => ({
          id: nextMsgId(),
          role: m.role === 'user' ? 'user' : 'assistant',
          content: typeof m.content === 'string' ? m.content : '',
          thinking: '',
          toolCalls: [],
          usage: null,
          steps: null,
          streaming: false,
          error: null,
          historical: true,
        }))
        socket?.send({ type: 'restore', messages: history, session_id: id })
        logSeq = 0
        set({ messages: restored, activeSessionId: id, logs: [] })
        toast('success', `已恢复会话（${restored.length} 条消息）`)
      } catch (e) {
        toast('error', `恢复会话失败：${e instanceof Error ? e.message : String(e)}`)
      }
    },

    deleteSession: async (id: string) => {
      try {
        await api.deleteSession(id)
        set((s) => ({
          sessions: s.sessions.filter((x) => x.id !== id),
          activeSessionId: s.activeSessionId === id ? null : s.activeSessionId,
        }))
        toast('success', '会话已删除')
      } catch (e) {
        toast('error', `删除失败：${e instanceof Error ? e.message : String(e)}`)
      }
    },
  }
})
