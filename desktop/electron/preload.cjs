/**
 * Nexus Desktop - Preload Script
 *
 * 在渲染进程和主进程之间建立安全的通信桥梁。
 * 通过 contextBridge 暴露有限的 API，避免直接暴露 Node.js 能力。
 */

const { contextBridge, ipcRenderer } = require('electron');

// 暴露平台信息和窗口控制 API
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,

  // 窗口控制
  windowMinimize: () => ipcRenderer.send('window:minimize'),
  windowClose: () => ipcRenderer.send('window:close'),

  // 获取平台信息（异步）
  getPlatform: () => ipcRenderer.invoke('window:get-platform'),
});
