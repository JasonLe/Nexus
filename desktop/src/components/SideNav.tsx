import { motion } from 'framer-motion'
import { useAppStore, type ViewKey } from '../store/appStore'

const NAV_ITEMS: Array<{ key: ViewKey; label: string; icon: string }> = [
  { key: 'chat', label: '会话', icon: '◈' },
  { key: 'config', label: '配置', icon: '◉' },
  { key: 'tools', label: '工具', icon: '' },
  { key: 'mcp', label: 'MCP', icon: '⛓' },
]

export function SideNav() {
  const view = useAppStore((s) => s.view)
  const setView = useAppStore((s) => s.setView)

  return (
    <nav className="relative z-10 flex w-14 shrink-0 flex-col items-center gap-1.5 border-r border-abyss-600/60 bg-abyss-850/60 py-3">
      {NAV_ITEMS.map((item) => {
        const active = view === item.key
        return (
          <button
            key={item.key}
            type="button"
            onClick={() => setView(item.key)}
            title={item.label}
            className={`relative flex h-11 w-11 flex-col items-center justify-center gap-0.5 rounded-lg transition-colors ${
              active ? 'text-neon-400' : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {active && (
              <motion.span
                layoutId="nav-active"
                className="absolute inset-0 rounded-lg border border-neon-500/40 bg-neon-500/[0.08] shadow-glow-neon"
                transition={{ type: 'spring', stiffness: 480, damping: 38 }}
              />
            )}
            <span className="relative text-[15px] leading-none">{item.icon}</span>
            <span className="relative font-display text-[9.5px] tracking-widest">
              {item.label}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
