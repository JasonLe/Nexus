import { AnimatePresence, motion } from 'framer-motion'
import { useToastStore, type ToastKind } from '../store/toastStore'

const KIND_CLS: Record<ToastKind, string> = {
  success: 'border-neon-500/50 text-neon-300 shadow-glow-neon',
  error: 'border-bloodx-500/50 text-bloodx-400 shadow-glow-red',
  info: 'border-abyss-600 text-slate-300',
}

const KIND_ICON: Record<ToastKind, string> = {
  success: '✓',
  error: '✕',
  info: 'ℹ',
}

export function Toasts() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)

  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[100] flex flex-col items-end gap-2">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.button
            key={t.id}
            type="button"
            initial={{ opacity: 0, x: 24, scale: 0.96 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 24, scale: 0.96 }}
            transition={{ duration: 0.18 }}
            onClick={() => dismiss(t.id)}
            className={`pointer-events-auto flex items-center gap-2 rounded-lg border bg-abyss-850/95 px-3.5 py-2 text-[12.5px] backdrop-blur-sm ${KIND_CLS[t.kind]}`}
          >
            <span className="font-mono text-[11px]">{KIND_ICON[t.kind]}</span>
            <span>{t.text}</span>
          </motion.button>
        ))}
      </AnimatePresence>
    </div>
  )
}
