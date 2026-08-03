import { useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useAppStore } from './store/appStore'
import { useChatStore } from './store/chatStore'
import { SideNav } from './components/SideNav'
import { TopBar } from './components/TopBar'
import { Toasts } from './components/Toasts'
import { ChatView } from './views/ChatView'
import { ConfigView } from './views/ConfigView'
import { ToolsView } from './views/ToolsView'

export default function App() {
  const view = useAppStore((s) => s.view)
  const refreshHealth = useAppStore((s) => s.refreshHealth)
  const initChat = useChatStore((s) => s.init)

  useEffect(() => {
    initChat()
    void refreshHealth()
    const timer = window.setInterval(() => void refreshHealth(), 15000)
    return () => window.clearInterval(timer)
  }, [initChat, refreshHealth])

  return (
    <div className="relative flex h-full flex-col">
      <div className="atmosphere" />
      <TopBar />
      <div className="relative z-10 flex min-h-0 flex-1">
        <SideNav />
        <main className="flex min-w-0 flex-1 flex-col">
          <AnimatePresence mode="wait">
            <motion.div
              key={view}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18, ease: 'easeOut' }}
              className="flex min-h-0 flex-1 flex-col"
            >
              {view === 'chat' && <ChatView />}
              {view === 'config' && <ConfigView />}
              {view === 'tools' && <ToolsView />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Toasts />
    </div>
  )
}
