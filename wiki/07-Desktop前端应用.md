# 07 - Desktop 前端应用

> 模块路径：`desktop/`
> 技术栈：React + TypeScript + Tailwind CSS + Zustand + Electron
> 教学视角：Desktop 是 Server 的「可视化外壳」，它本身不做 AI 决策，只负责将 WebSocket 事件流转化为用户可读的界面。理解这一点，就能理解前后端的分工边界。

---

## 1. 项目定位与技术选型

Desktop 是 Nexus 的**图形化桌面应用**，基于 Electron 构建，内部加载前端 React SPA。

### 技术栈选择原因

| 技术 | 用途 | 选择原因 |
|------|------|----------|
| React | UI 框架 | 组件化、生态成熟、与 Web 技术对齐 |
| TypeScript | 类型安全 | 前后端 API 契约的类型一致性 |
| Tailwind CSS | 样式方案 | 原子化、快速迭代、暗色主题易实现 |
| Zustand | 状态管理 | 轻量、无样板代码、支持异步 action |
| Electron | 桌面壳 | 跨平台、前端技术栈复用、系统 API 访问 |
| Vite | 构建工具 | 快速 HMR、生产优化 |

### 目录结构

```
desktop/
├── src/
│   ├── api/
│   │   ├── client.ts      # REST API 客户端
│   │   ├── types.ts       # 前后端共享类型定义
│   │   └── ws.ts          # WebSocket 连接管理
│   ├── store/
│   │   ├── chatStore.ts   # 聊天状态管理（核心）
│   │   └── toastStore.ts  # Toast 通知状态
│   ├── components/
│   │   ├── ChatView.tsx       # 主聊天界面
│   │   ├── ChatInput.tsx      # 输入框（含斜杠命令）
│   │   ├── SideNav.tsx        # 左侧会话导航
│   │   ├── MessageBubble.tsx  # 消息气泡
│   │   ├── LogDrawer.tsx      # 右侧日志抽屉
│   │   └── ...
│   ├── App.tsx
│   └── main.tsx
├── electron/
│   └── main.js            # Electron 主进程入口
├── index.html
├── package.json
├── tailwind.config.js
└── tsconfig.json
```

---

## 2. 状态管理核心 —— chatStore.ts

[`desktop/src/store/chatStore.ts`](file:///Users/wanghanle/Documents/code/githubProject/Nexus/desktop/src/store/chatStore.ts) 是 Desktop 前端**最核心的状态模块**，使用 Zustand 管理全局聊天状态。

### 状态结构

```typescript
interface ChatState {
  messages: ChatMessage[]      // 消息列表（用户 + AI）
  wsStatus: WsStatus           // WebSocket 连接状态
  running: boolean             // 是否有任务在执行
  sessions: SessionSummary[]   // 历史会话列表
  sessionsLoading: boolean     // 会话列表加载中
  activeSessionId: string | null  // 当前活跃会话 ID
  logs: LogEntry[]             // 日志条目（用于 LogDrawer）
}
```

### ChatMessage 结构

```typescript
interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string              // 回复内容（增量累积）
  thinking: string             // 思考链内容（增量累积）
  toolCalls: ToolCallRecord[]  // 工具调用记录
  usage: UsageInfo | null      // Token 用量
  steps: number | null         // 执行步数
  streaming: boolean           // 是否仍在流式接收
  error: string | null         // 错误信息
  historical: boolean          // 是否来自历史会话恢复
}
```

### WebSocket 消息处理 handleWsMessage()

这是**前后端联调的核心协议**，每个 case 对应 Server 推送的一种事件类型：

```typescript
function handleWsMessage(msg: WsServerMessage): void {
  const last = currentAssistant()  // 获取当前正在渲染的 assistant 消息
  
  switch (msg.type) {
    case 'thinking_delta':
      addLog('llm', 'thinking', msg.delta)
      if (last?.streaming) {
        patchAssistant(last.id, { thinking: last.thinking + msg.delta })
      }
      break
      
    case 'content_delta':
      addLog('llm', 'content', msg.delta)
      if (last?.streaming) {
        patchAssistant(last.id, { content: last.content + msg.delta })
      }
      break
      
    case 'tool_call':
      addLog('tool', msg.name, JSON.stringify(msg.args))
      if (last?.streaming) {
        patchAssistant(last.id, {
          toolCalls: [...last.toolCalls, { name: msg.name, args: msg.args, result: msg.result, ... }]
        })
      }
      break
      
    case 'usage':
      addLog('event', 'usage', `${msg.total_tokens} tokens`)
      if (last?.streaming) {
        patchAssistant(last.id, { usage: { prompt_tokens, completion_tokens, total_tokens } })
      }
      break
      
    case 'done':
      addLog('event', 'done', `steps=${msg.steps}`)
      if (last?.streaming) {
        patchAssistant(last.id, { streaming: false, steps: msg.steps, usage: msg.usage })
      }
      set({ running: false })
      get().loadSessions()           // 刷新会话列表
      if (msg.session_id) {
        set({ activeSessionId: msg.session_id })  // 标记当前会话
      }
      break
      
    case 'error':
      addLog('error', 'error', msg.message)
      if (last?.streaming) {
        patchAssistant(last.id, { streaming: false, error: msg.message })
      }
      set({ running: false })
      break
      
    case 'slash_command_result':
      addLog('event', msg.command, msg.title)
      // 添加一条 assistant 消息展示命令结果
      set((s) => ({ messages: [...s.messages, { role: 'assistant', content: `**${msg.title}**\n\n...`, streaming: false }] }))
      break
  }
}
```

### 关键操作

#### send() —— 发送消息

```typescript
send: (content: string) => {
  // 1. 校验：空内容 / 正在运行 / 未连接
  // 2. 添加 user 消息到列表
  // 3. 添加空的 assistant 消息（streaming=true）
  // 4. 设置 running=true
  // 5. 通过 WebSocket 发送 { type: 'message', content }
}
```

#### restoreSession() —— 恢复历史会话

```typescript
restoreSession: async (id: string) => {
  const detail = await api.getSession(id)
  const history = detail.messages.filter(m => m.role === 'user' || m.role === 'assistant')
  
  // 将历史消息转为 ChatMessage（historical=true）
  const restored = history.map(m => ({ ...emptyAssistant(), role: m.role, content: m.content, historical: true }))
  
  // 关键：发送 restore 消息给后端，携带原始 session_id
  socket?.send({ type: 'restore', messages: history, session_id: id })
  
  set({ messages: restored, activeSessionId: id, logs: [] })
}
```

#### newSession() —— 新建会话

```typescript
newSession: () => {
  socket?.send({ type: 'reset' })
  set({ messages: [], running: false, activeSessionId: null, logs: [] })
}
```

---

## 3. WebSocket 连接管理 —— ws.ts

`desktop/src/api/ws.ts` 封装了 WebSocket 的连接、重连、消息收发逻辑。

### 连接策略

```typescript
class ChatSocket {
  private ws: WebSocket | null = null
  private reconnectTimer: number | null = null
  private history: HistoryMessage[] = []  // 用于重连后恢复上下文
  
  connect() {
    this.ws = new WebSocket('ws://localhost:8321/ws/chat')
    this.ws.onopen = () => { ... }
    this.ws.onmessage = (e) => { this.onMessage(JSON.parse(e.data)) }
    this.ws.onclose = () => { this.scheduleReconnect() }  // 自动重连
    this.ws.onerror = () => { ... }
  }
  
  send(data: WsClientMessage): boolean {
    if (!this.ready) return false
    this.ws!.send(JSON.stringify(data))
    return true
  }
}
```

**重连机制**：
- 连接断开时自动触发重连（指数退避）
- 重连成功后发送 `restore` 消息恢复上下文（`buildHistoryForRestore()`）

---

## 4. REST API 客户端 —— client.ts

`desktop/src/api/client.ts` 封装了与 Server REST API 的通信。

```typescript
export const api = {
  getConfig: () => fetch('/api/config').then(r => r.json()),
  updateConfig: (data) => fetch('/api/config', { method: 'PUT', body: JSON.stringify(data) }),
  listSessions: () => fetch('/api/sessions').then(r => r.json()),
  getSession: (id) => fetch(`/api/sessions/${id}`).then(r => r.json()),
  deleteSession: (id) => fetch(`/api/sessions/${id}`, { method: 'DELETE' }),
  listTools: () => fetch('/api/tools').then(r => r.json()),
  listMcpServers: () => fetch('/api/mcp').then(r => r.json()),
  createMcpServer: (data) => fetch('/api/mcp', { method: 'POST', body: JSON.stringify(data) }),
  // ...
}
```

---

## 5. 前后端数据流全景

```
┌─────────────┐     WebSocket      ┌─────────────┐     EventBus     ┌─────────────┐
│   Desktop   │ <───────────────> │   Server    │ <──────────────> │    Agent    │
│   (React)   │   thinking_delta   │  (FastAPI)  │   LLM_CHUNK      │   (Core)    │
│             │   content_delta    │             │   AFTER_TOOL_CALL│             │
│             │   tool_call        │             │   AFTER_LLM_CALL │             │
│             │   usage            │             │   ON_ERROR       │             │
│             │   done             │             │   ON_FINISH      │             │
│             │   error            │             │                  │             │
└─────────────┘                    └─────────────┘                  └─────────────┘
       │                                  │
       │ REST API                         │
       ▼                                  ▼
┌─────────────┐                    ┌─────────────┐
│  /api/config │                    │  SessionManager│
│  /api/sessions│                   │  (持久化)      │
│  /api/tools  │                    └─────────────┘
│  /api/mcp    │
└─────────────┘
```

---

## 6. Electron 集成要点

### 主进程 electron/main.js

- 创建 BrowserWindow，加载 `http://localhost:8321/`（开发）或本地 HTML（生产）
- 自定义标题栏：`frame: false`（macOS 和 Windows 都禁用系统标题栏）
- 窗口控制按钮（最小化/关闭）由前端渲染，通过 IPC 调用 Electron API
- 初始窗口尺寸：1100×720（已根据用户反馈调整）

### 自定义标题栏

```
┌─────────────────────────────────────────────────────────────┐
│  Nexus Agent    openai / gpt-4o-mini      ● ●  ─  ✕        │
│  (左：状态信息)                           (右：窗口控制)      │
└─────────────────────────────────────────────────────────────┘
```

- 左侧：Logo、当前 provider/model、连接状态
- 右侧：最小化、关闭按钮
- 整行支持拖动（`-webkit-app-region: drag`）

---

## 7. 前端组件架构

### 核心组件职责

| 组件 | 职责 |
|------|------|
| `ChatView.tsx` | 主聊天界面，包含消息列表、输入框、日志抽屉开关 |
| `ChatInput.tsx` | 用户输入框，支持斜杠命令自动补全（`/` 触发下拉菜单） |
| `SideNav.tsx` | 左侧会话导航栏，展示历史会话列表、新建会话按钮、底部状态信息 |
| `MessageBubble.tsx` | 单条消息气泡，区分 user/assistant，展示 thinking、toolCalls |
| `LogDrawer.tsx` | 右侧日志抽屉，实时显示日志、支持搜索和按类型过滤 |
| `EmptyState.tsx` | 空状态展示（无消息时显示 "NEXUS AGENT"） |

### 斜杠命令自动补全

```typescript
// ChatInput.tsx
// 1. 监听输入，以 '/' 开头时显示命令下拉菜单
// 2. 支持方向键选择、Enter 发送、Escape 关闭、点击发送
// 3. 命令列表：/tools, /clear, /help, /sessions
```

---

## 8. 与 Server 的协作边界

| 职责 | Desktop（前端） | Server（后端） |
|------|-----------------|----------------|
| AI 决策 | ❌ 不处理 | ✅ Agent.run() |
| 流式渲染 | ✅ 增量更新 DOM | ✅ 推送 WebSocket 事件 |
| 状态持久化 | ❌ 不处理 | ✅ SessionManager.save() |
| 配置管理 | ✅ UI 编辑表单 | ✅ PUT /api/config + YAML 持久化 |
| 工具执行 | ❌ 不处理 | ✅ ToolExecutor |
| 会话列表 | ✅ 展示 | ✅ /api/sessions |
| MCP 管理 | ✅ UI 增删改 | ✅ /api/mcp + MCPPlugin |

---

## 9. 依赖关系

```
desktop/src/
    ├── api/client.ts
    │   └── 无内部依赖（原生 fetch）
    ├── api/types.ts
    │   └── 无内部依赖（纯类型定义）
    ├── api/ws.ts
    │   └── api/types.ts
    ├── store/chatStore.ts
    │   ├── api/client.ts
    │   ├── api/types.ts
    │   ├── api/ws.ts
    │   └── store/toastStore.ts
    └── store/toastStore.ts
        └── 无内部依赖
```
