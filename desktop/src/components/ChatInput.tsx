import { useRef, useState, useEffect } from 'react'
import type { KeyboardEvent } from 'react'
import { useChatStore } from '../store/chatStore'

interface SlashCommand {
  name: string
  description: string
}

const SLASH_COMMANDS: SlashCommand[] = [
  { name: '/tools', description: '列出当前已注册的工具' },
  { name: '/clear', description: '清空对话上下文' },
  { name: '/help', description: '显示帮助信息' },
  { name: '/sessions', description: '列出历史会话' },
]

export function ChatInput() {
  const [value, setValue] = useState('')
  const [showCommands, setShowCommands] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const running = useChatStore((s) => s.running)
  const wsStatus = useChatStore((s) => s.wsStatus)
  const send = useChatStore((s) => s.send)
  const sendSlashCommand = useChatStore((s) => s.sendSlashCommand)
  const taRef = useRef<HTMLTextAreaElement>(null)

  const disabled = running || wsStatus !== 'open'

  // 过滤匹配的命令
  const filteredCommands = SLASH_COMMANDS.filter((cmd) =>
    cmd.name.toLowerCase().startsWith(value.toLowerCase())
  )

  // 当输入变化时重置选择
  useEffect(() => {
    setSelectedIndex(0)
  }, [value])

  function doSend(): void {
    const text = value.trim()
    if (!text || disabled) return

    // 如果是斜杠命令，使用专门的发送方法
    if (text.startsWith('/')) {
      sendSlashCommand(text)
    } else {
      send(text)
    }

    setValue('')
    setShowCommands(false)
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>): void {
    // 如果命令列表显示中
    if (showCommands && filteredCommands.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((prev) => (prev + 1) % filteredCommands.length)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length)
        return
      }
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault()
        const selected = filteredCommands[selectedIndex]
        if (selected) {
          sendSlashCommand(selected.name)
          setValue('')
          setShowCommands(false)
        }
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setShowCommands(false)
        return
      }
    }

    // 正常发送
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      doSend()
    }
  }

  function autoResize(): void {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }

  function handleChange(newValue: string): void {
    setValue(newValue)

    // 检测是否应该显示命令列表
    const trimmed = newValue.trim()
    if (trimmed.startsWith('/') && trimmed.length > 0) {
      setShowCommands(true)
    } else {
      setShowCommands(false)
    }
  }

  function selectCommand(command: SlashCommand): void {
    sendSlashCommand(command.name)
    setValue('')
    setShowCommands(false)
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  return (
    <div className="border-t border-abyss-600/60 bg-abyss-850/70 px-5 py-3.5 backdrop-blur-sm">
      <div className="mx-auto flex max-w-4xl items-end gap-3">
        <div className="relative flex-1">
          <div className="panel !rounded-xl px-3.5 py-2.5 focus-within:border-neon-500/50 focus-within:shadow-glow-neon transition-shadow">
            <textarea
              ref={taRef}
              value={value}
              rows={1}
              disabled={disabled}
              placeholder={
                wsStatus !== 'open'
                  ? '连接中…'
                  : running
                    ? '执行中…'
                    : '输入消息，Enter 发送，Shift+Enter 换行；输入 / 查看命令'
              }
              onChange={(e) => {
                handleChange(e.target.value)
                autoResize()
              }}
              onKeyDown={onKeyDown}
              className="max-h-[180px] w-full resize-none bg-transparent text-[13.5px] leading-relaxed text-slate-200 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed"
            />
          </div>

          {/* 斜杠命令自动补全下拉框 */}
          {showCommands && filteredCommands.length > 0 && (
            <div className="absolute bottom-full left-0 right-0 mb-2 rounded-lg border border-abyss-600/60 bg-abyss-850/95 backdrop-blur-sm shadow-lg overflow-hidden">
              {filteredCommands.map((cmd, index) => (
                <button
                  key={cmd.name}
                  type="button"
                  onClick={() => selectCommand(cmd)}
                  onMouseEnter={() => setSelectedIndex(index)}
                  className={`w-full px-3 py-2 text-left flex items-center gap-3 transition-colors ${
                    index === selectedIndex
                      ? 'bg-neon-500/10 text-neon-400'
                      : 'text-slate-300 hover:bg-abyss-700/50'
                  }`}
                >
                  <span className="font-mono text-[13px] font-semibold">{cmd.name}</span>
                  <span className="text-[12px] text-slate-500 flex-1">{cmd.description}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={doSend}
          disabled={disabled || value.trim().length === 0}
          className="btn-primary h-[42px] shrink-0 px-5"
        >
          {running ? '执行中…' : '发送 ⏎'}
        </button>
      </div>
    </div>
  )
}
