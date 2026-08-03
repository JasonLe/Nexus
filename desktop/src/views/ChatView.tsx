import { useEffect, useRef } from 'react'
import { useChatStore } from '../store/chatStore'
import { MessageItem } from '../components/MessageItem'
import { ChatInput } from '../components/ChatInput'
import { SessionPanel } from '../components/SessionPanel'

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-neon-500/40 bg-neon-500/[0.06] font-display text-2xl font-bold text-neon-400 shadow-glow-neon">
        N
      </div>
      <div className="font-display text-[15px] font-semibold tracking-[0.2em] text-slate-300">
        NEXUS AGENT
      </div>
      <p className="max-w-sm text-[12.5px] leading-relaxed text-slate-500">
        在下方输入任务，Nexus 将自主规划、调用工具并逐步完成。
        左侧可恢复历史会话。
      </p>
    </div>
  )
}

export function ChatView() {
  const messages = useChatStore((s) => s.messages)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)

  // 流式追加时自动滚动（用户上翻时暂停跟随）
  useEffect(() => {
    const el = scrollRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [messages])

  function onScroll(): void {
    const el = scrollRef.current
    if (!el) return
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  return (
    <div className="flex h-full min-w-0 flex-1">
      {/* 历史会话侧栏 */}
      <aside className="hidden w-60 shrink-0 border-r border-abyss-600/60 bg-abyss-850/40 pt-3 md:block">
        <SessionPanel />
      </aside>

      {/* 消息流 + 输入 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto px-5 py-5">
          <div className="mx-auto max-w-4xl">
            {messages.length === 0 ? (
              <EmptyState />
            ) : (
              messages.map((m) => <MessageItem key={m.id} msg={m} />)
            )}
          </div>
        </div>
        <ChatInput />
      </div>
    </div>
  )
}
