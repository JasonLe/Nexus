import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { NexusConfigDto, ProviderConfig } from '../api/types'
import { useAppStore } from '../store/appStore'
import { toast } from '../store/toastStore'
import { Toggle } from '../components/Toggle'

interface EditableProvider extends ProviderConfig {
  /** 用户新输入的 key；空串 = 不修改 */
  newApiKey: string
}

interface EditableConfig {
  providers: Record<string, EditableProvider>
  default_provider: string
  agent: { system_prompt: string; max_steps: number }
  tools: { enabled: string[] }
  stream: boolean
}

function toEditable(dto: NexusConfigDto): EditableConfig {
  const providers: Record<string, EditableProvider> = {}
  for (const [name, p] of Object.entries(dto.providers)) {
    providers[name] = { ...p, newApiKey: '' }
  }
  return {
    providers,
    default_provider: dto.default_provider,
    agent: { ...dto.agent },
    tools: { enabled: [...dto.tools.enabled] },
    stream: dto.stream,
  }
}

function toDto(cfg: EditableConfig): NexusConfigDto {
  const providers: Record<string, ProviderConfig> = {}
  for (const [name, p] of Object.entries(cfg.providers)) {
    providers[name] = {
      api_key: p.newApiKey.trim() !== '' ? p.newApiKey.trim() : p.api_key,
      has_api_key: p.has_api_key,
      model: p.model,
      max_tokens: p.max_tokens,
      context_window_tokens: p.context_window_tokens,
      base_url: p.base_url,
    }
  }
  return {
    providers,
    default_provider: cfg.default_provider,
    agent: { ...cfg.agent },
    tools: { enabled: [...cfg.tools.enabled] },
    stream: cfg.stream,
  }
}

export function ConfigView() {
  const tools = useAppStore((s) => s.tools)
  const refreshTools = useAppStore((s) => s.refreshTools)
  const [config, setConfig] = useState<EditableConfig | null>(null)
  const [snapshot, setSnapshot] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const dirty = useMemo(
    () => config !== null && JSON.stringify(config) !== snapshot,
    [config, snapshot],
  )

  async function load(): Promise<void> {
    setLoading(true)
    try {
      const dto = await api.getConfig()
      const editable = toEditable(dto)
      setConfig(editable)
      setSnapshot(JSON.stringify(editable))
    } catch (e) {
      toast('error', `加载配置失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    void refreshTools()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function patchProvider(name: string, patch: Partial<EditableProvider>): void {
    setConfig((c) =>
      c
        ? {
            ...c,
            providers: { ...c.providers, [name]: { ...c.providers[name], ...patch } },
          }
        : c,
    )
  }

  function isToolEnabled(name: string): boolean {
    if (!config) return false
    return config.tools.enabled.length === 0 || config.tools.enabled.includes(name)
  }

  function setToolEnabled(name: string, enabled: boolean): void {
    setConfig((c) => {
      if (!c) return c
      const allNames = tools.map((t) => t.name)
      let next: string[]
      if (c.tools.enabled.length === 0) {
        // 当前为「全部启用」，从中移除 / 保持
        next = enabled ? [] : allNames.filter((n) => n !== name)
      } else if (enabled) {
        const set = new Set([...c.tools.enabled, name])
        // 全部勾选后回归「全部启用」语义（空数组）
        next = allNames.every((n) => set.has(n)) ? [] : [...set]
      } else {
        next = c.tools.enabled.filter((n) => n !== name)
      }
      return { ...c, tools: { enabled: next } }
    })
  }

  async function save(): Promise<void> {
    if (!config) return
    setSaving(true)
    try {
      const dto = await api.putConfig(toDto(config))
      const editable = toEditable(dto)
      setConfig(editable)
      setSnapshot(JSON.stringify(editable))
      toast('success', '配置已保存并生效')
    } catch (e) {
      toast('error', `保存失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading && !config) {
    return (
      <div className="flex h-full items-center justify-center font-mono text-[12px] text-slate-500">
        配置加载中…
      </div>
    )
  }
  if (!config) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        <span className="text-[13px] text-bloodx-400">配置加载失败</span>
        <button type="button" className="btn-ghost" onClick={() => void load()}>
          重试
        </button>
      </div>
    )
  }

  const providerNames = Object.keys(config.providers)
  const allEnabled = config.tools.enabled.length === 0

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="mx-auto max-w-3xl space-y-6 pb-8">
        {/* 标题栏 */}
        <div className="flex items-center gap-3">
          <h1 className="font-display text-[16px] font-bold tracking-[0.18em] text-slate-100">
            配置
          </h1>
          {dirty && (
            <span className="flex items-center gap-1.5 rounded border border-amberx-500/40 bg-amberx-500/10 px-2 py-0.5 font-mono text-[10.5px] text-amberx-400">
              <span className="pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-amberx-400 text-amberx-400" />
              有未保存的修改
            </span>
          )}
          <div className="ml-auto flex gap-2">
            <button type="button" className="btn-ghost" onClick={() => void load()}>
              重新加载
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!dirty || saving}
              onClick={() => void save()}
            >
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </div>

        {/* Providers */}
        <section>
          <div className="section-label mb-2">模型提供商 / PROVIDERS</div>
          <div className="space-y-3">
            {providerNames.map((name) => {
              const p = config.providers[name]
              const isDefault = config.default_provider === name
              return (
                <div
                  key={name}
                  className={`panel p-4 transition-colors ${
                    isDefault ? '!border-neon-500/40 shadow-glow-neon' : ''
                  }`}
                >
                  <div className="mb-3 flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => setConfig({ ...config, default_provider: name })}
                      className="flex items-center gap-2"
                      title="设为默认 Provider"
                    >
                      <span
                        className={`flex h-3.5 w-3.5 items-center justify-center rounded-full border ${
                          isDefault ? 'border-neon-400' : 'border-slate-600'
                        }`}
                      >
                        {isDefault && (
                          <span className="h-1.5 w-1.5 rounded-full bg-neon-400" />
                        )}
                      </span>
                      <span
                        className={`font-display text-[14px] font-bold tracking-wider ${
                          isDefault ? 'text-neon-300' : 'text-slate-200'
                        }`}
                      >
                        {name}
                      </span>
                    </button>
                    {isDefault && (
                      <span className="rounded bg-neon-500/10 px-1.5 py-px font-mono text-[10px] uppercase tracking-wider text-neon-400">
                        默认
                      </span>
                    )}
                    <span
                      className={`ml-auto font-mono text-[10.5px] ${
                        p.has_api_key ? 'text-neon-500/80' : 'text-slate-600'
                      }`}
                    >
                      {p.has_api_key ? `KEY ${p.api_key ?? ''}` : '未配置 KEY'}
                    </span>
                  </div>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    <label className="block">
                      <span className="section-label mb-1 block">Model</span>
                      <input
                        className="field-input font-mono"
                        value={p.model}
                        onChange={(e) => patchProvider(name, { model: e.target.value })}
                      />
                    </label>
                    <label className="block">
                      <span className="section-label mb-1 block">API Key</span>
                      <input
                        type="password"
                        className="field-input font-mono"
                        placeholder={p.has_api_key ? '已配置（留空不修改）' : '未配置'}
                        value={p.newApiKey}
                        onChange={(e) => patchProvider(name, { newApiKey: e.target.value })}
                      />
                    </label>
                    <label className="block">
                      <span className="section-label mb-1 block">Max Tokens</span>
                      <input
                        type="number"
                        className="field-input font-mono"
                        value={p.max_tokens}
                        onChange={(e) =>
                          patchProvider(name, { max_tokens: Number(e.target.value) || 0 })
                        }
                      />
                    </label>
                    <label className="block">
                      <span className="section-label mb-1 block">Context Window Tokens</span>
                      <input
                        type="number"
                        className="field-input font-mono"
                        value={p.context_window_tokens}
                        onChange={(e) =>
                          patchProvider(name, {
                            context_window_tokens: Number(e.target.value) || 0,
                          })
                        }
                      />
                    </label>
                    <label className="block md:col-span-2">
                      <span className="section-label mb-1 block">Base URL</span>
                      <input
                        className="field-input font-mono"
                        placeholder="默认（官方端点）"
                        value={p.base_url ?? ''}
                        onChange={(e) =>
                          patchProvider(name, { base_url: e.target.value || null })
                        }
                      />
                    </label>
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {/* Agent */}
        <section>
          <div className="section-label mb-2">Agent</div>
          <div className="panel space-y-3 p-4">
            <label className="block">
              <span className="section-label mb-1 block">System Prompt</span>
              <textarea
                rows={4}
                className="field-input resize-y leading-relaxed"
                value={config.agent.system_prompt}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    agent: { ...config.agent, system_prompt: e.target.value },
                  })
                }
              />
            </label>
            <label className="block w-48">
              <span className="section-label mb-1 block">Max Steps</span>
              <input
                type="number"
                min={1}
                className="field-input font-mono"
                value={config.agent.max_steps}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    agent: { ...config.agent, max_steps: Number(e.target.value) || 1 },
                  })
                }
              />
            </label>
          </div>
        </section>

        {/* 工具开关 */}
        <section>
          <div className="section-label mb-2">工具 / TOOLS</div>
          <div className="panel p-4">
            <div className="mb-3 flex items-center justify-between border-b border-abyss-600/50 pb-3">
              <Toggle
                checked={allEnabled}
                onChange={(on) =>
                  setConfig({ ...config, tools: { enabled: on ? [] : ['__none__'] } })
                }
                label="全部启用"
              />
              <span className="font-mono text-[10.5px] text-slate-500">
                {allEnabled
                  ? 'enabled = []（不限制）'
                  : `已启用 ${config.tools.enabled.filter((n) => tools.some((t) => t.name === n)).length} 项`}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {tools.map((t) => (
                <div
                  key={t.name}
                  className="flex items-center justify-between rounded-lg border border-abyss-600/50 bg-abyss-900/50 px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="font-mono text-[12px] text-slate-200">{t.name}</div>
                    <div className="truncate text-[11px] text-slate-500">{t.description}</div>
                  </div>
                  <Toggle
                    checked={isToolEnabled(t.name)}
                    onChange={(on) => setToolEnabled(t.name, on)}
                  />
                </div>
              ))}
              {tools.length === 0 && (
                <div className="py-2 font-mono text-[11px] text-slate-600">
                  未获取到工具列表（后端未连接？）
                </div>
              )}
            </div>
          </div>
        </section>

        {/* 流式开关 */}
        <section>
          <div className="section-label mb-2">输出 / OUTPUT</div>
          <div className="panel p-4">
            <Toggle
              checked={config.stream}
              onChange={(on) => setConfig({ ...config, stream: on })}
              label="流式输出（实时显示生成内容）"
            />
          </div>
        </section>
      </div>
    </div>
  )
}
