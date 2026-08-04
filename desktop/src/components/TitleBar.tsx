import { useAppStore } from '../store/appStore'
import { useChatStore } from '../store/chatStore'
import type { WsStatus } from '../api/ws'

const STATUS_META: Record<WsStatus, { label: string; cls: string; pulse: boolean }> = {
  open: { label: '已连接', cls: 'bg-neon-400', pulse: false },
  connecting: { label: '连接中', cls: 'bg-amberx-400', pulse: true },
  closed: { label: '已断开', cls: 'bg-bloodx-400', pulse: false },
}

export function TitleBar() {
  const health = useAppStore((s) => s.health)
  const healthError = useAppStore((s) => s.healthError)
  const wsStatus = useChatStore((s) => s.wsStatus)
  const meta = STATUS_META[wsStatus]

  const handleMinimize = () => {
    window.electronAPI?.windowMinimize()
  }

  const handleClose = () => {
    window.electronAPI?.windowClose()
  }

  return (
    <header
      className="relative z-10 flex h-8 shrink-0 items-center justify-between border-b border-abyss-600/60 bg-abyss-850/80 px-4"
      style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
    >
      {/* 左侧：Logo + 状态信息 */}
      <div className="flex items-center gap-2.5">
        <div className="flex h-5 w-5 items-center justify-center rounded border border-neon-500/50 bg-neon-500/10 font-display text-[11px] font-bold text-neon-400">
          N
        </div>
        <span className="font-display text-[12px] font-bold tracking-[0.14em] text-slate-100">
          NEXUS
        </span>

        {health && (
          <div className="ml-2 flex items-center gap-1.5 font-mono text-[10px] text-slate-400">
            <span className="text-slate-600">provider</span>
            <span className="text-neon-300">{health.provider}</span>
            <span className="text-slate-600">/</span>
            <span className="text-slate-300">{health.model}</span>
          </div>
        )}
        {healthError && (
          <span className="ml-2 font-mono text-[10px] text-bloodx-400">后端不可达</span>
        )}
        <div className="flex items-center gap-1">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${meta.cls} ${
              meta.pulse ? 'pulse-dot' : ''
            }`}
          />
          <span className="font-mono text-[10px] text-slate-400">{meta.label}</span>
        </div>
      </div>

      {/* 右侧：窗口控制按钮 */}
      <div className="flex items-center gap-1" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
        <button
          type="button"
          onClick={handleMinimize}
          className="flex h-6 w-8 items-center justify-center rounded text-slate-400 hover:bg-abyss-700 hover:text-slate-200"
          title="最小化"
        >
          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M20 12H4" />
          </svg>
        </button>
        <button
          type="button"
          onClick={handleClose}
          className="flex h-6 w-8 items-center justify-center rounded text-slate-400 hover:bg-bloodx-500 hover:text-white"
          title="关闭"
        >
          <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </header>
  )
}
