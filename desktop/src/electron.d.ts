/**
 * Electron preload API type declarations
 *
 * These APIs are exposed via contextBridge in electron/preload.cjs
 */

export interface ElectronAPI {
  /** Current platform: 'darwin' | 'win32' | 'linux' */
  platform: NodeJS.Platform
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
