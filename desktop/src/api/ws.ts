import type { HistoryMessage, WsClientMessage, WsServerMessage } from './types'

export type WsStatus = 'connecting' | 'open' | 'closed'

interface ChatSocketHandlers {
  onMessage: (msg: WsServerMessage) => void
  onStatus: (status: WsStatus) => void
  /** 重连成功后回调（用于恢复对话上下文） */
  onReconnected?: () => HistoryMessage[] | null
}

function wsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/ws/chat`
}

/** 带自动重连的聊天 WebSocket 客户端 */
export class ChatSocket {
  private ws: WebSocket | null = null
  private handlers: ChatSocketHandlers
  private reconnectTimer: number | null = null
  private everConnected = false
  private manuallyClosed = false

  constructor(handlers: ChatSocketHandlers) {
    this.handlers = handlers
  }

  get ready(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }

  connect(): void {
    this.manuallyClosed = false
    this.handlers.onStatus('connecting')
    const ws = new WebSocket(wsUrl())
    this.ws = ws

    ws.onopen = () => {
      const isReconnect = this.everConnected
      this.everConnected = true
      this.handlers.onStatus('open')
      if (isReconnect && this.handlers.onReconnected) {
        // 每个连接独立维护对话历史，重连后需要重新 restore
        const history = this.handlers.onReconnected()
        if (history && history.length > 0) {
          this.send({ type: 'restore', messages: history })
        }
      }
    }

    ws.onmessage = (ev: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(ev.data) as WsServerMessage
        this.handlers.onMessage(msg)
      } catch {
        // 忽略无法解析的帧
      }
    }

    ws.onclose = () => {
      this.ws = null
      if (this.manuallyClosed) {
        this.handlers.onStatus('closed')
        return
      }
      this.handlers.onStatus('connecting')
      this.scheduleReconnect()
    }

    ws.onerror = () => {
      ws.close()
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, 2000)
  }

  send(msg: WsClientMessage): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(msg))
    return true
  }

  dispose(): void {
    this.manuallyClosed = true
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }
}
