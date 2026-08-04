import { create } from 'zustand'
import { api } from '../api/client'
import type { HealthInfo, McpServerDto, McpServerInput, ToolInfo } from '../api/types'

export type ViewKey = 'chat' | 'config' | 'tools' | 'mcp'

type McpPatch = Partial<Omit<McpServerInput, 'name'>>

interface AppState {
  view: ViewKey
  health: HealthInfo | null
  healthError: boolean
  tools: ToolInfo[]
  mcpServers: McpServerDto[]
  mcpLoading: boolean
  mcpError: string | null
  setView: (view: ViewKey) => void
  refreshHealth: () => Promise<void>
  refreshTools: () => Promise<void>
  refreshMcp: () => Promise<void>
  createMcp: (input: McpServerInput) => Promise<void>
  updateMcp: (name: string, patch: McpPatch) => Promise<void>
  toggleMcp: (name: string, enabled: boolean) => Promise<void>
  removeMcp: (name: string) => Promise<void>
  reconnectMcp: (name: string) => Promise<void>
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export const useAppStore = create<AppState>((set, get) => ({
  view: 'chat',
  health: null,
  healthError: false,
  tools: [],
  mcpServers: [],
  mcpLoading: false,
  mcpError: null,
  setView: (view) => set({ view }),
  refreshHealth: async () => {
    try {
      const health = await api.health()
      set({ health, healthError: false })
    } catch {
      set({ health: null, healthError: true })
    }
  },
  refreshTools: async () => {
    try {
      const tools = await api.listTools()
      set({ tools })
    } catch {
      set({ tools: [] })
    }
  },
  refreshMcp: async () => {
    set({ mcpLoading: true, mcpError: null })
    try {
      const servers = await api.listMcp()
      set({ mcpServers: servers })
    } catch (e) {
      set({ mcpError: errorMessage(e) })
    } finally {
      set({ mcpLoading: false })
    }
  },
  createMcp: async (input) => {
    try {
      await api.createMcp(input)
      await get().refreshMcp()
    } catch (e) {
      set({ mcpError: errorMessage(e) })
      throw e
    }
  },
  updateMcp: async (name, patch) => {
    try {
      await api.updateMcp(name, patch)
      await get().refreshMcp()
    } catch (e) {
      set({ mcpError: errorMessage(e) })
      throw e
    }
  },
  toggleMcp: async (name, enabled) => {
    await get().updateMcp(name, { enabled })
  },
  removeMcp: async (name) => {
    try {
      await api.deleteMcp(name)
      await get().refreshMcp()
    } catch (e) {
      set({ mcpError: errorMessage(e) })
      throw e
    }
  },
  reconnectMcp: async (name) => {
    try {
      await api.reconnectMcp(name)
      await get().refreshMcp()
    } catch (e) {
      set({ mcpError: errorMessage(e) })
      throw e
    }
  },
}))
