import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useAppStore } from '../store/appStore'
import { toast } from '../store/toastStore'
import { Toggle } from '../components/Toggle'
import type {
  McpServerDto,
  McpServerInput,
  McpTestResult,
  McpTransport,
} from '../api/types'

interface FormState {
  name: string
  transport: McpTransport
  command: string
  args: string
  url: string
  env: string
}

const EMPTY_FORM: FormState = {
  name: '',
  transport: 'stdio',
  command: '',
  args: '',
  url: '',
  env: '',
}

/** env（掩码值）转 textarea 文本 */
function envToText(env: Record<string, string> | null): string {
  if (!env) return ''
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join('\n')
}

/** 每行 KEY=VALUE 的 textarea 文本解析为 env 字典 */
function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('#')) continue
    const idx = t.indexOf('=')
    if (idx <= 0) continue
    out[t.slice(0, idx).trim()] = t.slice(idx + 1).trim()
  }
  return out
}

/**
 * args 数组 → textarea 文本（每行一个参数）。
 * 相比空格拼接：可表达带空格/引号的单参数（如 ``--with mcp<2``、
 * ``--config "some path"``），避免 ``split(/\s+/)`` 丢失引号分组。
 */
function argsToText(args: string[] | null | undefined): string {
  return (args ?? []).join('\n')
}

/** textarea 文本 → args 数组（每行一个参数，去空行） */
function parseArgs(text: string): string[] {
  return text
    .split('\n')
    .map((t) => t.trim())
    .filter(Boolean)
}

interface StatusMeta {
  label: string
  cls: string
  dot: string
}

function statusMeta(status: string): StatusMeta {
  switch (status) {
    case 'connected':
      return {
        label: '已连接',
        cls: 'border-neon-500/40 bg-neon-500/10 text-neon-400',
        dot: 'bg-neon-400 shadow-glow-neon',
      }
    case 'error':
      return {
        label: '错误',
        cls: 'border-bloodx-500/40 bg-bloodx-500/10 text-bloodx-400',
        dot: 'bg-bloodx-400 shadow-glow-red',
      }
    case 'disabled':
      return {
        label: '已禁用',
        cls: 'border-abyss-600/70 bg-abyss-700/40 text-slate-500',
        dot: 'bg-slate-500',
      }
    default:
      return {
        label: status,
        cls: 'border-amberx-500/40 bg-amberx-500/10 text-amberx-400',
        dot: 'bg-amberx-400 shadow-glow-amber',
      }
  }
}

export function McpView() {
  const servers = useAppStore((s) => s.mcpServers)
  const loading = useAppStore((s) => s.mcpLoading)
  const mcpError = useAppStore((s) => s.mcpError)
  const refreshMcp = useAppStore((s) => s.refreshMcp)
  const createMcp = useAppStore((s) => s.createMcp)
  const updateMcp = useAppStore((s) => s.updateMcp)
  const toggleMcp = useAppStore((s) => s.toggleMcp)
  const removeMcp = useAppStore((s) => s.removeMcp)
  const reconnectMcp = useAppStore((s) => s.reconnectMcp)
  const testMcp = useAppStore((s) => s.testMcp)

  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<McpServerDto | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<McpTestResult | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  useEffect(() => {
    void refreshMcp()
  }, [refreshMcp])

  function resetForm(): void {
    setForm(EMPTY_FORM)
    setEditing(null)
    setFormError(null)
    setTestResult(null)
  }

  function openCreate(): void {
    resetForm()
    setShowForm(true)
  }

  function closeForm(): void {
    setShowForm(false)
    resetForm()
  }

  function openEdit(server: McpServerDto): void {
    setForm({
      name: server.name,
      transport: server.transport,
      command: server.command ?? '',
      args: argsToText(server.args),
      url: server.url ?? '',
      env: envToText(server.env),
    })
    setEditing(server)
    setFormError(null)
    setTestResult(null)
    setShowForm(true)
  }

  /** 从当前表单构造请求体（始终携带 name；编辑态提交时调用方去除 name） */
  function buildInput(): McpServerInput {
    const input: McpServerInput = {
      name: form.name.trim(),
      args: parseArgs(form.args),
      command: form.transport === 'stdio' ? form.command.trim() : null,
      url: form.transport === 'http' ? form.url.trim() : null,
    }
    const env = parseEnv(form.env)
    if (Object.keys(env).length > 0) input.env = env
    return input
  }

  async function handleTest(): Promise<void> {
    const name = form.name.trim()
    if (!name) {
      setFormError('名称不能为空')
      return
    }
    if (form.transport === 'stdio' && !form.command.trim()) {
      setFormError('命令（command）不能为空')
      return
    }
    if (form.transport === 'http' && !form.url.trim()) {
      setFormError('URL 不能为空')
      return
    }
    setTesting(true)
    setTestResult(null)
    setFormError(null)
    try {
      const result = await testMcp(buildInput())
      setTestResult(result)
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e))
    } finally {
      setTesting(false)
    }
  }

  async function handleSubmit(): Promise<void> {
    const name = form.name.trim()
    if (!name) {
      setFormError('名称不能为空')
      return
    }
    if (form.transport === 'stdio' && !form.command.trim()) {
      setFormError('命令（command）不能为空')
      return
    }
    if (form.transport === 'http' && !form.url.trim()) {
      setFormError('URL 不能为空')
      return
    }
    setSubmitting(true)
    setFormError(null)
    try {
      let dto: McpServerDto
      if (editing) {
        const { name: _ignored, ...patch } = buildInput()
        const typedPatch: Partial<Omit<McpServerInput, 'name'>> = patch
        // env 仍为掩码回显文本（未修改）时不发送，避免覆盖后端未变更项
        if (form.env.trim() !== '' && form.env !== envToText(editing.env)) {
          typedPatch.env = parseEnv(form.env)
        }
        dto = await updateMcp(name, typedPatch)
        echoStatus(dto, `已更新 MCP 服务器「${name}」`)
      } else {
        const input: McpServerInput = buildInput()
        input.enabled = true
        dto = await createMcp(input)
        echoStatus(dto, `已添加 MCP 服务器「${name}」`)
      }
      closeForm()
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  /** 保存成功后按返回的运行状态回显：connected → 工具数；error → 失败详情 */
  function echoStatus(dto: McpServerDto, okMessage: string): void {
    if (dto.status === 'connected') {
      toast('success', `${okMessage}，已连接并注册 ${dto.tool_count} 个工具`)
    } else if (dto.status === 'error') {
      toast('error', `${okMessage}，但连接失败：${dto.error ?? '未知错误'}`)
    } else {
      toast('success', okMessage)
    }
  }

  async function handleToggle(server: McpServerDto, enabled: boolean): Promise<void> {
    try {
      await toggleMcp(server.name, enabled)
      toast('success', enabled ? `已启用「${server.name}」` : `已禁用「${server.name}」`)
    } catch (e) {
      toast('error', `操作失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  async function handleReconnect(server: McpServerDto): Promise<void> {
    try {
      await reconnectMcp(server.name)
      toast('success', `已重连「${server.name}」`)
    } catch (e) {
      toast('error', `重连失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  function handleRemove(server: McpServerDto): void {
    if (
      !window.confirm(
        `确定卸载 MCP 服务器「${server.name}」？\n该操作会断开连接并注销其所有工具。`,
      )
    ) {
      return
    }
    void (async () => {
      try {
        await removeMcp(server.name)
        toast('success', `已卸载「${server.name}」`)
      } catch (e) {
        toast('error', `卸载失败：${e instanceof Error ? e.message : String(e)}`)
      }
    })()
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="mx-auto max-w-3xl space-y-5 pb-8">
        {/* 标题栏 */}
        <div className="flex items-center gap-3">
          <h1 className="font-display text-[16px] font-bold tracking-[0.18em] text-slate-100">
            MCP 服务器
          </h1>
          <span className="rounded border border-abyss-600/70 px-2 py-0.5 font-mono text-[10.5px] text-slate-500">
            {servers.length} 个已配置
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              className="btn-ghost px-3 py-1.5 text-[12px]"
              disabled={loading}
              onClick={() => void refreshMcp()}
            >
              {loading ? '刷新中…' : '刷新'}
            </button>
            <button
              type="button"
              className="btn-primary px-3 py-1.5 text-[12px]"
              onClick={showForm ? closeForm : openCreate}
            >
              {showForm ? '取消' : '+ 添加服务器'}
            </button>
          </div>
        </div>

        {/* 全局错误横幅 */}
        {mcpError && (
          <div className="rounded-lg border border-bloodx-500/40 bg-bloodx-500/10 px-3 py-2 font-mono text-[11.5px] text-bloodx-400">
            操作失败：{mcpError}
          </div>
        )}

        {/* 添加 / 编辑表单 */}
        <AnimatePresence initial={false}>
          {showForm && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
              className="overflow-hidden"
            >
              <div className="panel space-y-4 p-4">
                <div className="section-label">
                  {editing ? `编辑服务器 / ${editing.name}` : '添加服务器 / NEW SERVER'}
                </div>

                <label className="block">
                  <span className="section-label mb-1 block">名称 *</span>
                  <input
                    className="field-input font-mono"
                    placeholder="如 filesystem / my-server"
                    value={form.name}
                    disabled={!!editing}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                  />
                </label>

                {/* 传输方式 */}
                <div>
                  <span className="section-label mb-1 block">传输方式</span>
                  <div className="flex gap-2">
                    {(['stdio', 'http'] as McpTransport[]).map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => setForm({ ...form, transport: t })}
                        className={`rounded-lg border px-4 py-1.5 font-display text-[12px] font-semibold tracking-wider transition-colors ${
                          form.transport === t
                            ? 'border-neon-500/50 bg-neon-500/10 text-neon-400'
                            : 'border-abyss-600/70 text-slate-500 hover:text-slate-300'
                        }`}
                      >
                        {t === 'stdio' ? 'STDIO' : 'HTTP'}
                      </button>
                    ))}
                  </div>
                </div>

                {form.transport === 'stdio' ? (
                  <>
                    <label className="block">
                      <span className="section-label mb-1 block">命令（Command）*</span>
                      <input
                        className="field-input font-mono"
                        placeholder="如 npx / uvx / node"
                        value={form.command}
                        onChange={(e) => setForm({ ...form, command: e.target.value })}
                      />
                    </label>
                    <label className="block">
                      <span className="section-label mb-1 block">参数（Args）</span>
                      <textarea
                        rows={4}
                        className="field-input resize-y font-mono"
                        placeholder={'每行一个参数\n如 -y\n@modelcontextprotocol/server-filesystem\n.'}
                        value={form.args}
                        onChange={(e) => setForm({ ...form, args: e.target.value })}
                      />
                    </label>
                    <label className="block">
                      <span className="section-label mb-1 block">
                        环境变量（Env）
                        {editing && '（掩码值回显，未修改则不覆盖）'}
                      </span>
                      <textarea
                        rows={3}
                        className="field-input resize-y font-mono"
                        placeholder={'每行一个 KEY=VALUE\n如 ANTHROPIC_API_KEY=sk-xxx'}
                        value={form.env}
                        onChange={(e) => setForm({ ...form, env: e.target.value })}
                      />
                    </label>
                  </>
                ) : (
                  <label className="block">
                    <span className="section-label mb-1 block">URL *</span>
                    <input
                      className="field-input font-mono"
                      placeholder="如 http://localhost:8000/mcp"
                      value={form.url}
                      onChange={(e) => setForm({ ...form, url: e.target.value })}
                    />
                  </label>
                )}

                {formError && (
                  <div className="font-mono text-[11.5px] text-bloodx-400">{formError}</div>
                )}

                {/* 连接测试结果 */}
                {testResult && (
                  <div
                    className={`rounded-md border px-2.5 py-2 font-mono text-[11px] leading-relaxed ${
                      testResult.ok
                        ? 'border-neon-500/30 bg-neon-500/[0.06] text-neon-400/90'
                        : 'border-bloodx-500/30 bg-bloodx-500/[0.06] text-bloodx-400/90'
                    }`}
                  >
                    {testResult.ok ? (
                      <>
                        <span className="text-neon-400">连接成功</span>
                        {testResult.tools.length > 0 && (
                          <span className="text-slate-500">，发现 {testResult.tools.length} 个工具：</span>
                        )}
                        {testResult.tools.length > 0 && (
                          <div className="mt-1 space-y-0.5">
                            {testResult.tools.slice(0, 8).map((t) => (
                              <div key={t}>· {t}</div>
                            ))}
                            {testResult.tools.length > 8 && (
                              <div className="text-slate-600">… 共 {testResult.tools.length} 个</div>
                            )}
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="whitespace-pre-wrap">连接失败：{testResult.error}</div>
                    )}
                  </div>
                )}

                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    className="btn-ghost px-4 py-1.5 text-[12px]"
                    disabled={testing || submitting}
                    onClick={() => void handleTest()}
                  >
                    {testing ? '测试中…' : '测试连接'}
                  </button>
                  <button type="button" className="btn-ghost px-4 py-1.5 text-[12px]" onClick={closeForm}>
                    取消
                  </button>
                  <button
                    type="button"
                    className="btn-primary px-4 py-1.5 text-[12px]"
                    disabled={submitting || testing}
                    onClick={() => void handleSubmit()}
                  >
                    {submitting ? '提交中…' : editing ? '保存修改' : '添加'}
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* 空状态 */}
        {servers.length === 0 && !showForm && (
          <div className="panel p-8 text-center">
            <div className="mb-2 text-[22px] text-think-400">⛓</div>
            <div className="font-display text-[13px] font-semibold tracking-wider text-slate-300">
              尚未配置任何 MCP 服务器
            </div>
            <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">
              点击「+ 添加服务器」接入模型上下文协议（MCP）服务器，
              <br />
              为 Agent 扩展文件、搜索与外部系统能力。
            </p>
          </div>
        )}

        {/* 服务器卡片列表 */}
        <div className="space-y-3">
          {servers.map((server, i) => {
            const meta = statusMeta(server.status)
            const isExpanded = !!expanded[server.name]
            return (
              <motion.div
                key={server.name}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.22, delay: Math.min(i * 0.05, 0.3), ease: 'easeOut' }}
                className="panel p-4"
              >
                {/* 头部：名称 / 徽标 / 开关 */}
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-md border border-think-500/40 bg-think-500/[0.08] text-[13px] text-think-400">
                    ⛓
                  </span>
                  <span className="font-display text-[14px] font-bold tracking-wide text-slate-100">
                    {server.name}
                  </span>
                  <span
                    className={`rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wider ${
                      server.transport === 'http'
                        ? 'border-sky-500/40 bg-sky-500/10 text-sky-400'
                        : 'border-abyss-600/70 bg-abyss-700/40 text-slate-400'
                    }`}
                  >
                    {server.transport}
                  </span>
                  <span
                    className={`flex items-center gap-1.5 rounded border px-1.5 py-px font-mono text-[10px] ${meta.cls}`}
                  >
                    <span className={`inline-block h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                    {meta.label}
                  </span>
                  <span className="font-mono text-[10.5px] text-slate-500">
                    {server.tool_count} 个工具
                  </span>
                  <div className="ml-auto">
                    <Toggle
                      checked={server.enabled}
                      onChange={(on) => void handleToggle(server, on)}
                      label={server.enabled ? '启用' : '禁用'}
                    />
                  </div>
                </div>

                {/* 命令 / URL 摘要 */}
                {(server.transport === 'stdio' && server.command) ||
                (server.transport === 'http' && server.url) ? (
                  <div className="mt-2 truncate font-mono text-[11px] text-slate-500">
                    {server.transport === 'stdio'
                      ? `${server.command} ${server.args.join(' ')}`
                      : server.url}
                  </div>
                ) : null}

                {/* 错误详情 */}
                {server.error && (
                  <div className="mt-2 rounded-md border border-bloodx-500/30 bg-bloodx-500/[0.06] px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-bloodx-400/90">
                    {server.error}
                  </div>
                )}

                {/* 操作按钮 */}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="btn-ghost px-3 py-1 text-[11.5px]"
                    onClick={() =>
                      setExpanded((e) => ({ ...e, [server.name]: !e[server.name] }))
                    }
                  >
                    {isExpanded ? '收起工具' : `工具（${server.tool_count}）`}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost px-3 py-1 text-[11.5px]"
                    disabled={!server.enabled}
                    onClick={() => void handleReconnect(server)}
                  >
                    重连
                  </button>
                  <button
                    type="button"
                    className="btn-ghost px-3 py-1 text-[11.5px]"
                    onClick={() => openEdit(server)}
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    className="btn-ghost px-3 py-1 text-[11.5px] !border-bloodx-500/40 !text-bloodx-400 hover:!border-bloodx-500/70 hover:!text-bloodx-400"
                    onClick={() => handleRemove(server)}
                  >
                    卸载
                  </button>
                </div>

                {/* 工具列表（折叠） */}
                <AnimatePresence initial={false}>
                  {isExpanded && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.2, ease: 'easeOut' }}
                      className="overflow-hidden"
                    >
                      <div className="mt-3 space-y-1.5 rounded-lg border border-abyss-600/60 bg-abyss-900/50 p-2.5">
                        {server.tools.length === 0 && (
                          <div className="py-1 font-mono text-[11px] text-slate-600">
                            暂无工具（未连接或无工具注册）
                          </div>
                        )}
                        {server.tools.map((tool) => (
                          <div
                            key={tool.name}
                            className="flex items-start gap-2 rounded-md bg-abyss-950/40 px-2.5 py-1.5"
                          >
                            <span className="mt-[3px] h-1.5 w-1.5 shrink-0 rounded-full bg-neon-500/70" />
                            <div className="min-w-0">
                              <div className="font-mono text-[11.5px] text-neon-300">
                                {tool.name}
                              </div>
                              {tool.description && (
                                <div className="mt-0.5 text-[11px] leading-relaxed text-slate-500">
                                  {tool.description}
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
