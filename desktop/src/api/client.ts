import type {
  HealthInfo,
  NexusConfigDto,
  SessionDetail,
  SessionSummary,
  ToolInfo,
} from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // 忽略非 JSON 错误体
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

export const api = {
  health: () => request<HealthInfo>('/health'),
  getConfig: () => request<NexusConfigDto>('/config'),
  putConfig: (cfg: NexusConfigDto) =>
    request<NexusConfigDto>('/config', { method: 'PUT', body: JSON.stringify(cfg) }),
  listSessions: () => request<SessionSummary[]>('/sessions'),
  getSession: (id: string) => request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`),
  deleteSession: (id: string) =>
    request<{ ok: boolean }>(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  listTools: () => request<ToolInfo[]>('/tools'),
}
