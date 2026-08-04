/**
 * Electron preload API type declarations
 *
 * These APIs are exposed via contextBridge in electron/preload.cjs
 */

export interface ElectronAPI {
  /** Current platform: 'darwin' | 'win32' | 'linux' */
  platform: NodeJS.Platform

  /** Minimize the main window */
  windowMinimize: () => void

  /** Close the main window */
  windowClose: () => void

  /** Get the current platform (async version) */
  getPlatform: () => Promise<NodeJS.Platform>
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
