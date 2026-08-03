import { useEffect } from 'react'
import { motion } from 'framer-motion'
import { useAppStore } from '../store/appStore'
import type { ToolInfo } from '../api/types'

function SchemaTable({ tool }: { tool: ToolInfo }) {
  const props = tool.schema?.properties ?? {}
  const required = new Set(tool.schema?.required ?? [])
  const names = Object.keys(props)

  if (names.length === 0) {
    return <div className="mt-3 font-mono text-[11px] text-slate-600">无参数</div>
  }

  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-abyss-600/60">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr className="bg-abyss-750/80">
            <th className="border-b border-abyss-600/60 px-3 py-1.5 text-left font-display text-[10.5px] uppercase tracking-[0.18em] text-slate-400">
              参数
            </th>
            <th className="border-b border-abyss-600/60 px-3 py-1.5 text-left font-display text-[10.5px] uppercase tracking-[0.18em] text-slate-400">
              类型
            </th>
            <th className="border-b border-abyss-600/60 px-3 py-1.5 text-left font-display text-[10.5px] uppercase tracking-[0.18em] text-slate-400">
              必填
            </th>
            <th className="border-b border-abyss-600/60 px-3 py-1.5 text-left font-display text-[10.5px] uppercase tracking-[0.18em] text-slate-400">
              描述
            </th>
          </tr>
        </thead>
        <tbody>
          {names.map((name) => {
            const prop = props[name]
            const type =
              prop.type === 'array' && prop.items?.type
                ? `${prop.type}<${prop.items.type}>`
                : (prop.type ?? 'any')
            return (
              <tr key={name} className="odd:bg-abyss-900/40">
                <td className="border-b border-abyss-600/40 px-3 py-1.5 font-mono text-neon-300">
                  {name}
                </td>
                <td className="border-b border-abyss-600/40 px-3 py-1.5 font-mono text-slate-400">
                  {type}
                </td>
                <td className="border-b border-abyss-600/40 px-3 py-1.5">
                  {required.has(name) ? (
                    <span className="font-mono text-[11px] text-amberx-400">是</span>
                  ) : (
                    <span className="font-mono text-[11px] text-slate-600">否</span>
                  )}
                </td>
                <td className="border-b border-abyss-600/40 px-3 py-1.5 text-slate-400">
                  {prop.description ?? '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function ToolsView() {
  const tools = useAppStore((s) => s.tools)
  const refreshTools = useAppStore((s) => s.refreshTools)

  useEffect(() => {
    void refreshTools()
  }, [refreshTools])

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="mx-auto max-w-3xl pb-8">
        <div className="mb-4 flex items-center gap-3">
          <h1 className="font-display text-[16px] font-bold tracking-[0.18em] text-slate-100">
            工具
          </h1>
          <span className="rounded border border-abyss-600/70 px-2 py-0.5 font-mono text-[10.5px] text-slate-500">
            {tools.length} 个已注册
          </span>
          <button
            type="button"
            className="btn-ghost ml-auto px-3 py-1.5 text-[12px]"
            onClick={() => void refreshTools()}
          >
            刷新
          </button>
        </div>

        {tools.length === 0 && (
          <div className="panel p-8 text-center font-mono text-[12px] text-slate-600">
            未获取到工具列表（后端未连接？）
          </div>
        )}

        <div className="space-y-3">
          {tools.map((tool, i) => (
            <motion.div
              key={tool.name}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, delay: Math.min(i * 0.04, 0.3), ease: 'easeOut' }}
              className="panel p-4"
            >
              <div className="flex items-center gap-2.5">
                <span className="flex h-7 w-7 items-center justify-center rounded-md border border-neon-500/40 bg-neon-500/[0.08] text-[13px] text-neon-400">
                  ⬡
                </span>
                <span className="font-display text-[14px] font-bold tracking-wide text-slate-100">
                  {tool.name}
                </span>
              </div>
              <p className="mt-2 text-[12.5px] leading-relaxed text-slate-400">
                {tool.description}
              </p>
              <SchemaTable tool={tool} />
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  )
}
