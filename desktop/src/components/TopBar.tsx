import { useAppStore } from '../store/appStore'
import { useChatStore } from '../store/chatStore'
import type { WsStatus } from '../api/ws'

const STATUS_META: Record<WsStatus, { label: string; cls: string; pulse: boolean }> = {
  open: { label: '已连接', cls: 'bg-neon-400 text-neon-400', pulse: false },
  connecting: { label: '连接中', cls: 'bg-amberx-400 text-amberx-400', pulse: true },
  closed: { label: '已断开', cls: 'bg-bloodx-400 text-bloodx-400', pulse: false },
}

export function TopBar() {
  const health = useAppStore((s) => s.health)
  const healthError = useAppStore((s) => s.healthError)
  const wsStatus = useChatStore((s) => s.wsStatus)
  const meta = STATUS_META[wsStatus]

  return (
    <header className="relative z-10 flex h-12 shrink-0 items-center gap-4 border-b border-abyss-600/60 bg-abyss-850/80 px-5 backdrop-blur-sm">
      <div className="flex items-center gap-2.5">
        <div className="flex h-6 w-6 items-center justify-center rounded-md border border-neon-500/50 bg-neon-500/10 font-display text-[13px] font-bold text-neon-400 shadow-glow-neon">
          N
        </div>
        <span className="font-display text-[14px] font-bold tracking-[0.14em] text-slate-100">
          NEXUS
        </span>
        <span className="rounded border border-abyss-600/70 px-1.5 py-px font-mono text-[9.5px] uppercase tracking-widest text-slate-500">
          desktop
        </span>
      </div>

      <div className="ml-auto flex items-center gap-4">
        {health && (
          <div className="flex items-center gap-2 font-mono text-[11px] text-slate-400">
            <span className="text-slate-600">provider</span>
            <span className="text-neon-300">{health.provider}</span>
            <span className="text-slate-600">/</span>
            <span className="text-slate-300">{health.model}</span>
          </div>
        )}
        {healthError && (
          <span className="font-mono text-[11px] text-bloodx-400">后端不可达</span>
        )}
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${meta.cls} ${
              meta.pulse ? 'pulse-dot' : ''
            }`}
          />
          <span className="font-mono text-[11px] text-slate-400">{meta.label}</span>
        </div>
      </div>
    </header>
  )
}
