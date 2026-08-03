import { create } from 'zustand'
import { api } from '../api/client'
import type { HealthInfo, ToolInfo } from '../api/types'

export type ViewKey = 'chat' | 'config' | 'tools'

interface AppState {
  view: ViewKey
  health: HealthInfo | null
  healthError: boolean
  tools: ToolInfo[]
  setView: (view: ViewKey) => void
  refreshHealth: () => Promise<void>
  refreshTools: () => Promise<void>
}

export const useAppStore = create<AppState>((set) => ({
  view: 'chat',
  health: null,
  healthError: false,
  tools: [],
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
}))
