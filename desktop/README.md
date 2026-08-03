# Nexus Desktop

Nexus Agent 的桌面端：Vite + React 18 + TS + Tailwind 前端，Electron 桌面壳，
后端为 `nexus serve` 启动的 FastAPI 服务（REST `/api/*` + WebSocket `/ws/chat`，
默认 `127.0.0.1:8321`）。

渲染进程不持有任何 Node 能力，仅通过 HTTP/WS 与后端交互（无 preload/IPC）。

## 目录结构

- `src/` —— 前端源码（React）
- `dist/` —— 前端构建产物（`npm run build`）
- `electron/main.cjs` —— Electron 主进程（CommonJS）

## 开发模式（热更新）

三个进程配合：后端 + Vite dev server + Electron。

```powershell
# 1. 启动后端（Electron 也会自动拉起，手动启动便于查看日志）
nexus serve --port 8321

# 2. 启动 Vite dev server（5173 端口，/api 与 /ws 已代理到 8321）
npm run dev

# 3. 以开发模式启动 Electron，加载 dev server 页面
$env:NEXUS_DEV_SERVER_URL = 'http://localhost:5173'
npm run electron:start
```

主进程启动时会自动拉起 `nexus serve --port 8321`（找不到 `nexus` 命令时回退
`python -m nexus serve`），并轮询 `/api/health` 等待就绪后再开窗；
检测到 8321 已有后端在运行时直接复用，不会重复拉起。

## 生产模式

```powershell
# 1. 构建前端产物到 dist/
npm run build

# 2. 启动 Electron（自动拉起后端，加载 http://127.0.0.1:8321/）
npm run electron:start
```

生产模式下页面由 FastAPI 直接托管（`desktop/dist` 以 StaticFiles 挂载到 `/`），
无需单独的静态服务器。`npm run electron:dev` 等价于先 build 再 `electron .`。

不用 Electron 时也可以纯浏览器体验：`nexus ui` 会启动同一后端并自动打开浏览器。

## 冒烟测试（无显示器环境）

```powershell
npx electron . --smoke
```

启动后端 → 健康探测 → 加载页面，成功后打印 `SMOKE_OK` 并自动退出（退出码 0）；
失败打印 `SMOKE_FAIL` 并以非零码退出（整体超时 60s）。

## 打包（预留）

已配置 electron-builder（appId `com.nexus.desktop`，Windows NSIS）：

```powershell
npm run dist   # 产出安装包到 dist-electron/
```

注意：打包前需先 `npm run build`；NSIS 首次打包会联网下载依赖，
网络受限时可设置 `ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/`。
安装包内不包含 Python 后端，目标机器需另行安装 `nexus` CLI。
