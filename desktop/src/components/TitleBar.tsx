import { useEffect, useState } from 'react'
import { useAppStore } from '../store/appStore'
import { useChatStore } from '../store/chatStore'
import type { WsStatus } from '../api/ws'

const STATUS_META: Record<WsStatus, { label: string; cls: string; pulse: boolean }> = {
  open: { label: '已连接', cls: 'bg-neon-400', pulse: false },
  connecting: { label: '连接中', cls: 'bg-amberx-400', pulse: true },
  closed: { label: '已断开', cls: 'bg-bloodx-400', pulse: false },
}

/** Window Controls Overlay API（Electron 在 titleBarStyle:'hidden' 时提供，无内建 TS 类型） */
interface WindowControlsOverlay extends EventTarget {
  getTitlebarAreaRect: () => { x: number; y: number; width: number; height: number }
}

/** Windows 原生窗口控制按钮（右上角 overlay）的实测宽度，不可用时回退 138px */
const FALLBACK_CONTROLS_WIDTH = 138

function useNativeControlsWidth(): number {
  const [width, setWidth] = useState(FALLBACK_CONTROLS_WIDTH)

  useEffect(() => {
    const wco = (navigator as unknown as { windowControlsOverlay?: WindowControlsOverlay })
      .windowControlsOverlay
    if (!wco) return // 浏览器模式或非 overlay 窗口：无需避让

    const update = () => {
      const rect = wco.getTitlebarAreaRect()
      // 标题栏区域右侧到窗口右缘即为按钮占用宽度
      const controlsWidth = Math.round(window.innerWidth - rect.x - rect.width)
      setWidth(controlsWidth > 0 ? controlsWidth : FALLBACK_CONTROLS_WIDTH)
    }
    update()
    wco.addEventListener('geometrychange', update)
    window.addEventListener('resize', update)
    return () => {
      wco.removeEventListener('geometrychange', update)
      window.removeEventListener('resize', update)
    }
  }, [])

  return width
}

export function TitleBar() {
  const health = useAppStore((s) => s.health)
  const healthError = useAppStore((s) => s.healthError)
  const wsStatus = useChatStore((s) => s.wsStatus)
  const meta = STATUS_META[wsStatus]

  const isMac = window.electronAPI?.platform === 'darwin'
  const isWin = window.electronAPI?.platform === 'win32'
  // Windows：右侧为原生最小化/最大化/关闭按钮预留实测宽度；macOS：左侧避让交通灯
  const controlsWidth = useNativeControlsWidth()

  return (
    <header className="app-drag relative z-10 flex h-8 shrink-0 items-center justify-between border-b border-abyss-600/60 bg-abyss-850/80 px-4">
      {/* 左侧：Logo + 状态信息（窗口缩窄时自动截断，避免挤压原生按钮区域） */}
      <div
        className={`flex min-w-0 items-center gap-2.5 ${isMac ? 'pl-20' : ''}`}
      >
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-neon-500/50 bg-neon-500/10 font-display text-[11px] font-bold text-neon-400">
          N
        </div>
        <span className="shrink-0 font-display text-[12px] font-bold tracking-[0.14em] text-slate-100">
          NEXUS
        </span>

        {health && (
          <div className="ml-2 flex min-w-0 items-center gap-1.5 font-mono text-[10px] text-slate-400">
            <span className="shrink-0 text-slate-600">provider</span>
            <span className="truncate text-neon-300">{health.provider}</span>
            <span className="shrink-0 text-slate-600">/</span>
            <span className="truncate text-slate-300">{health.model}</span>
          </div>
        )}
        {healthError && (
          <span className="ml-2 shrink-0 font-mono text-[10px] text-bloodx-400">后端不可达</span>
        )}
        <div className="flex shrink-0 items-center gap-1">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${meta.cls} ${
              meta.pulse ? 'pulse-dot' : ''
            }`}
          />
          <span className="font-mono text-[10px] text-slate-400">{meta.label}</span>
        </div>
      </div>

      {/* 右侧：原生窗口控制按钮占位（Windows overlay 按钮由 OS 绘制在此区域之上） */}
      {isWin && <div className="shrink-0" style={{ width: controlsWidth }} />}
    </header>
  )
}
