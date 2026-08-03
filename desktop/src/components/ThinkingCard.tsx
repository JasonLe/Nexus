import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

interface Props {
  thinking: string
  streaming: boolean
}

/** 思考过程卡片：流式期间展开实时追加，完成后可折叠 */
export function ThinkingCard({ thinking, streaming }: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (streaming) setCollapsed(false)
  }, [streaming])

  useEffect(() => {
    const el = bodyRef.current
    if (el && !collapsed) el.scrollTop = el.scrollHeight
  }, [thinking, collapsed])

  if (!thinking) return null

  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-think-500/25 bg-think-500/[0.05]">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-think-400 text-think-400" />
        <span className="font-display text-[11px] font-semibold uppercase tracking-[0.18em] text-think-300/80">
          思考过程
        </span>
        <span className="ml-auto text-think-400/60 text-[11px] font-mono">
          {collapsed ? '▸' : '▾'}
        </span>
      </button>
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeInOut' }}
          >
            <div
              ref={bodyRef}
              className="max-h-48 overflow-y-auto border-t border-think-500/15 px-3 py-2"
            >
              <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-think-300/70">
                {thinking}
                {streaming && <span className="type-cursor think" />}
              </pre>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
