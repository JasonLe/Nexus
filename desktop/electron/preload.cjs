/**
 * Nexus Desktop - Preload Script
 *
 * 在渲染进程和主进程之间建立安全的通信桥梁。
 * 通过 contextBridge 暴露有限的 API，避免直接暴露 Node.js 能力。
 */

const { contextBridge } = require('electron');

// 窗口控制按钮由 OS 原生绘制（titleBarStyle: 'hidden' + titleBarOverlay），
// 此处仅暴露平台信息供渲染进程做平台相关的布局避让（如 macOS 交通灯在左侧）
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
});
