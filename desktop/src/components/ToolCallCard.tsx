import { useState } from 'react'
import { motion } from 'framer-motion'
import type { ToolCallRecord } from '../store/chatStore'

function summarize(value: unknown, max = 120): string {
  const s = typeof value === 'string' ? value : JSON.stringify(value)
  if (!s) return ''
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

export function ToolCallCard({ call }: { call: ToolCallRecord }) {
  const [open, setOpen] = useState(false)
  const ok = call.success
  const argsText = Object.entries(call.args)
    .map(([k, v]) => `${k}=${summarize(v, 60)}`)
    .join('  ')

  return (
    <motion.div
      initial={{ opacity: 0, y: 6, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
      className={`mb-2 overflow-hidden rounded-lg border bg-abyss-900/70 ${
        ok ? 'border-neon-500/25' : 'border-bloodx-500/35'
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        <span className={`font-mono text-[11px] ${ok ? 'text-neon-400' : 'text-bloodx-400'}`}>
          🔧
        </span>
        <span className="font-mono text-[11px] text-slate-500">[{call.index}]</span>
        <span
          className={`font-display text-[12px] font-semibold tracking-wide ${
            ok ? 'text-neon-300' : 'text-bloodx-400'
          }`}
        >
          {call.name}
        </span>
        <span
          className={`ml-1 rounded px-1.5 py-px font-mono text-[10px] uppercase tracking-wider ${
            ok
              ? 'bg-neon-500/10 text-neon-400'
              : 'bg-bloodx-500/10 text-bloodx-400'
          }`}
        >
          {ok ? '成功' : '失败'}
        </span>
        <span className="ml-auto truncate pl-3 font-mono text-[11px] text-slate-500 max-w-[45%]">
          {argsText}
        </span>
        <span className="text-slate-500 text-[11px] font-mono">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-abyss-600/50 px-3 py-2">
          <div>
            <div className="section-label mb-0.5">参数</div>
            <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] text-slate-400">
              {JSON.stringify(call.args, null, 2)}
            </pre>
          </div>
          {call.result && (
            <div>
              <div className="section-label mb-0.5">结果</div>
              <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] text-slate-300">
                {call.result}
              </pre>
            </div>
          )}
          {call.error && (
            <div>
              <div className="section-label mb-0.5 text-bloodx-400/80">错误</div>
              <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] text-bloodx-400">
                {call.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}
