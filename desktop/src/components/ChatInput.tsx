import { useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { useChatStore } from '../store/chatStore'

export function ChatInput() {
  const [value, setValue] = useState('')
  const running = useChatStore((s) => s.running)
  const wsStatus = useChatStore((s) => s.wsStatus)
  const send = useChatStore((s) => s.send)
  const taRef = useRef<HTMLTextAreaElement>(null)

  const disabled = running || wsStatus !== 'open'

  function doSend(): void {
    const text = value.trim()
    if (!text || disabled) return
    send(text)
    setValue('')
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>): void {
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

  return (
    <div className="border-t border-abyss-600/60 bg-abyss-850/70 px-5 py-3.5 backdrop-blur-sm">
      <div className="mx-auto flex max-w-4xl items-end gap-3">
        <div className="panel flex-1 !rounded-xl px-3.5 py-2.5 focus-within:border-neon-500/50 focus-within:shadow-glow-neon transition-shadow">
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
                  : '输入消息，Enter 发送，Shift+Enter 换行'
            }
            onChange={(e) => {
              setValue(e.target.value)
              autoResize()
            }}
            onKeyDown={onKeyDown}
            className="max-h-[180px] w-full resize-none bg-transparent text-[13.5px] leading-relaxed text-slate-200 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed"
          />
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
