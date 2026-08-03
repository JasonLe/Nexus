import { motion } from 'framer-motion'
import type { ChatMessage } from '../store/chatStore'
import { Markdown } from './Markdown'
import { ThinkingCard } from './ThinkingCard'
import { ToolCallCard } from './ToolCallCard'

function UsageFooter({ msg }: { msg: ChatMessage }) {
  if (msg.streaming || (!msg.usage && msg.steps === null)) return null
  return (
    <div className="mt-2 flex items-center gap-3 border-t border-abyss-600/40 pt-1.5 font-mono text-[10.5px] text-slate-500">
      {msg.steps !== null && <span>步数 {msg.steps}</span>}
      {msg.usage && (
        <span>
          tokens {msg.usage.prompt_tokens} → {msg.usage.completion_tokens}（共{' '}
          {msg.usage.total_tokens}）
        </span>
      )}
    </div>
  )
}

export function MessageItem({ msg }: { msg: ChatMessage }) {
  if (msg.role === 'user') {
    return (
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
        className="mb-4 flex justify-end"
      >
        <div className="max-w-[78%] rounded-xl rounded-br-sm border border-amberx-500/30 bg-amberx-500/[0.07] px-4 py-2.5 shadow-glow-amber">
          <div className="mb-1 flex items-center gap-1.5">
            <span className="text-[12px]">👤</span>
            <span className="font-display text-[10.5px] font-semibold uppercase tracking-[0.18em] text-amberx-400/90">
              你
            </span>
            {msg.historical && (
              <span className="ml-1 rounded bg-abyss-700/60 px-1 font-mono text-[9.5px] text-slate-500">
                历史
              </span>
            )}
          </div>
          <div className="whitespace-pre-wrap break-words text-[13.5px] leading-relaxed text-amberx-400/95">
            {msg.content}
          </div>
        </div>
      </motion.div>
    )
  }

  const showCursor = msg.streaming && msg.content.length > 0
  const showPending = msg.streaming && msg.content.length === 0 && !msg.error

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: 'easeOut' }}
      className="mb-4"
    >
      <div className="max-w-[88%] rounded-xl rounded-tl-sm border border-neon-500/20 bg-abyss-850/70 px-4 py-3">
        <div className="mb-1.5 flex items-center gap-1.5">
          <span className="text-[12px]">🤖</span>
          <span className="font-display text-[10.5px] font-semibold uppercase tracking-[0.18em] text-neon-400">
            Nexus
          </span>
          {msg.streaming && (
            <span className="pulse-dot ml-1 inline-block h-1.5 w-1.5 rounded-full bg-neon-400 text-neon-400" />
          )}
          {msg.historical && (
            <span className="ml-1 rounded bg-abyss-700/60 px-1 font-mono text-[9.5px] text-slate-500">
              历史
            </span>
          )}
        </div>

        <ThinkingCard thinking={msg.thinking} streaming={msg.streaming} />

        {msg.toolCalls.map((call) => (
          <ToolCallCard key={`${msg.id}-tc-${call.index}`} call={call} />
        ))}

        {msg.error ? (
          <div className="rounded-lg border border-bloodx-500/40 bg-bloodx-500/[0.07] px-3 py-2 text-[13px] text-bloodx-400 shadow-glow-red">
            ⚠ {msg.error}
          </div>
        ) : showPending ? (
          <div className="flex items-center gap-2 py-1 text-[13px] text-slate-500">
            <span className="type-cursor" />
            <span className="font-mono text-[12px]">执行中…</span>
          </div>
        ) : (
          <>
            <Markdown content={msg.content} />
            {showCursor && <span className="type-cursor" />}
          </>
        )}

        <UsageFooter msg={msg} />
      </div>
    </motion.div>
  )
}
