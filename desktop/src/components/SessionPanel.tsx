import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useChatStore } from '../store/chatStore'

function formatTime(ts: string | number): string {
  // 兼容 ISO 字符串与 epoch（秒/毫秒）
  let d: Date
  if (typeof ts === 'number') {
    d = new Date(ts > 1e12 ? ts : ts * 1000)
  } else {
    d = new Date(ts)
  }
  if (Number.isNaN(d.getTime())) return String(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function SessionPanel() {
  const sessions = useChatStore((s) => s.sessions)
  const loading = useChatStore((s) => s.sessionsLoading)
  const activeId = useChatStore((s) => s.activeSessionId)
  const restoreSession = useChatStore((s) => s.restoreSession)
  const deleteSession = useChatStore((s) => s.deleteSession)
  const loadSessions = useChatStore((s) => s.loadSessions)
  const newSession = useChatStore((s) => s.newSession)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 pb-2">
        <span className="section-label">历史会话</span>
        <button
          type="button"
          onClick={() => void loadSessions()}
          title="刷新列表"
          className="rounded p-1 text-slate-500 transition-colors hover:text-neon-400"
        >
          ⟳
        </button>
      </div>
      <button type="button" onClick={newSession} className="btn-primary mx-3 mb-2 py-1.5 text-[12px]">
        ＋ 新会话
      </button>

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {loading && sessions.length === 0 && (
          <div className="px-2 py-6 text-center font-mono text-[11px] text-slate-600">
            加载中…
          </div>
        )}
        {!loading && sessions.length === 0 && (
          <div className="px-2 py-6 text-center font-mono text-[11px] text-slate-600">
            暂无历史会话
          </div>
        )}
        {sessions.map((s) => {
          const active = s.id === activeId
          return (
            <div
              key={s.id}
              className={`group mb-1 cursor-pointer rounded-lg border px-2.5 py-2 transition-colors ${
                active
                  ? 'border-neon-500/40 bg-neon-500/[0.06]'
                  : 'border-transparent hover:border-abyss-600/70 hover:bg-abyss-800/60'
              }`}
              onClick={() => void restoreSession(s.id)}
            >
              <div className="flex items-center gap-2">
                <span
                  className={`flex-1 truncate text-[12.5px] ${
                    active ? 'text-neon-300' : 'text-slate-300'
                  }`}
                >
                  {s.summary || '（无摘要）'}
                </span>
                <button
                  type="button"
                  title="删除会话"
                  onClick={(e) => {
                    e.stopPropagation()
                    setConfirmId(s.id)
                  }}
                  className="hidden shrink-0 rounded px-1 text-slate-600 transition-colors hover:text-bloodx-400 group-hover:block"
                >
                  ✕
                </button>
              </div>
              <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-slate-500">
                <span>{formatTime(s.timestamp)}</span>
                <span>·</span>
                <span>{s.message_count} 条消息</span>
              </div>
            </div>
          )
        })}
      </div>

      {/* 删除二次确认 */}
      <AnimatePresence>
        {confirmId && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-abyss-950/70 backdrop-blur-sm"
            onClick={() => setConfirmId(null)}
          >
            <motion.div
              initial={{ scale: 0.94, y: 8 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.94, y: 8 }}
              transition={{ duration: 0.16 }}
              className="panel w-72 p-4"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-1 font-display text-[13px] font-semibold text-slate-200">
                删除会话？
              </div>
              <p className="mb-4 text-[12px] leading-relaxed text-slate-400">
                会话记录与日志文件将被一并删除，此操作不可恢复。
              </p>
              <div className="flex justify-end gap-2">
                <button type="button" className="btn-ghost px-3 py-1.5 text-[12px]" onClick={() => setConfirmId(null)}>
                  取消
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-bloodx-500/50 bg-bloodx-500/10 px-3 py-1.5 font-display text-[12px] font-semibold text-bloodx-400 transition-colors hover:bg-bloodx-500/20"
                  onClick={() => {
                    void deleteSession(confirmId)
                    setConfirmId(null)
                  }}
                >
                  确认删除
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
