import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useChatStore, type LogEntryType } from '../store/chatStore'

type FilterType = 'all' | LogEntryType

const FILTERS: Array<{ key: FilterType; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'llm', label: 'LLM' },
  { key: 'tool', label: 'Tool' },
  { key: 'event', label: 'Event' },
  { key: 'error', label: 'Error' },
]

const TYPE_COLORS: Record<LogEntryType, string> = {
  llm: 'text-neon-400',
  tool: 'text-cyan-400',
  event: 'text-slate-400',
  error: 'text-bloodx-400',
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

export function LogDrawer() {
  const logs = useChatStore((s) => s.logs)
  const [isOpen, setIsOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<FilterType>('all')
  const scrollRef = useRef<HTMLDivElement>(null)

  // 自动滚动到底部
  useEffect(() => {
    const el = scrollRef.current
    if (el && isOpen) {
      el.scrollTop = el.scrollHeight
    }
  }, [logs, isOpen])

  // 过滤日志
  const filteredLogs = logs.filter((log) => {
    if (filter !== 'all' && log.type !== filter) return false
    if (search) {
      const q = search.toLowerCase()
      return (
        log.content.toLowerCase().includes(q) ||
        log.label.toLowerCase().includes(q)
      )
    }
    return true
  })

  return (
    <>
      {/* 切换按钮（抽屉关闭时显示） */}
      {!isOpen && (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="absolute right-4 top-4 z-20 flex h-8 w-8 items-center justify-center rounded-lg border border-abyss-600/60 bg-abyss-800/80 text-slate-400 hover:text-slate-200 transition-colors"
          title="打开日志"
        >
          <svg
            className="h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
            />
          </svg>
        </button>
      )}

      {/* 抽屉 */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className="absolute right-0 top-0 z-10 flex h-full w-80 flex-col border-l border-abyss-600/60 bg-abyss-900/95 backdrop-blur-sm"
          >
            {/* 头部 */}
            <div className="flex items-center justify-between border-b border-abyss-600/60 px-4 py-3">
              <h3 className="font-display text-sm font-semibold text-slate-200">
                会话日志
              </h3>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:text-slate-200"
              >
                <svg
                  className="h-4 w-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* 搜索框 */}
            <div className="border-b border-abyss-600/60 px-4 py-2">
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索日志..."
                className="w-full rounded border border-abyss-600/60 bg-abyss-800/50 px-3 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:border-neon-500/50 focus:outline-none"
              />
            </div>

            {/* 筛选按钮 */}
            <div className="flex gap-1 border-b border-abyss-600/60 px-4 py-2">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  className={`rounded px-2 py-1 text-xs transition-colors ${
                    filter === f.key
                      ? 'bg-neon-500/20 text-neon-400'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>

            {/* 日志列表 */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-2">
              {filteredLogs.length === 0 ? (
                <div className="flex h-full items-center justify-center text-xs text-slate-500">
                  {logs.length === 0 ? '暂无日志' : '无匹配结果'}
                </div>
              ) : (
                <div className="space-y-2">
                  {filteredLogs.map((log) => (
                    <div
                      key={log.id}
                      className="rounded border border-abyss-600/40 bg-abyss-800/30 p-2"
                    >
                      <div className="flex items-center gap-2 text-xs">
                        <span className="font-mono text-slate-500">
                          {formatTime(log.timestamp)}
                        </span>
                        <span className={`font-mono font-medium ${TYPE_COLORS[log.type]}`}>
                          {log.label}
                        </span>
                      </div>
                      <div className="mt-1 break-all font-mono text-xs text-slate-300">
                        {log.content}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 底部统计 */}
            <div className="border-t border-abyss-600/60 px-4 py-2 text-xs text-slate-500">
              {filteredLogs.length} / {logs.length} 条日志
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
