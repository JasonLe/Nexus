# 06 - Server 与 Web 接口

> 模块路径：`nexus/server/app.py`
> 教学视角：Server 不是 CLI 的「替代品」，而是同一核心的**协议适配层**。理解 Server 的适配器定位，就能明白为什么它能和 CLI 共享 90% 以上的业务逻辑。

---

## 1. 模块定位

Server 模块是 Nexus 的 **HTTP/WebSocket 协议适配层**，职责单一明确：

- **不做决策**：所有 Agent 执行逻辑复用 `nexus.core`
- **只做适配**：将 HTTP 请求/响应、WebSocket 消息映射到 Agent API
- **状态挂载**：配置、Agent、SessionManager 挂在 `app.state` 上，路由通过 `request.app.state` 访问

### 启动方式

```bash
# 方式 1：通过 CLI
nexus serve [--port 8321] [--host 127.0.0.1]
nexus ui    # 启动服务 + 自动打开浏览器

# 方式 2：直接运行（开发调试）
python -c "from nexus.server.app import create_app; import uvicorn; uvicorn.run(create_app(), host='0.0.0.0', port=8321)"
```

---

## 2. 应用工厂 create_app()

[`nexus/server/app.py::create_app()`](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/server/app.py#L635) 是 Server 的核心工厂函数，采用**依赖注入**设计，便于测试隔离。

### 函数签名

```python
def create_app(
    work_dir: str | None = None,
    *,
    config: NexusConfig | None = None,      # 注入配置（测试用）
    llm: BaseLLM | None = None,              # 注入 LLM（mock 测试）
    agent: Agent | None = None,              # 注入 Agent（完整 mock）
    session_manager: SessionManager | None = None,  # 注入会话管理器
    config_save_path: str | None = None,     # 配置持久化路径
) -> FastAPI:
```

### 组装流程

```
1. 确定 work_dir（默认当前目录）
2. 加载/注入 config
3. 创建/注入 Agent（优先使用 create_agent_async）
4. 创建/注入 SessionManager
5. 注册 lifespan（启动时安装 MCP 插件）
6. 挂载 REST API 路由
7. 挂载 WebSocket 路由
8. 挂载静态资源（desktop/dist）
```

### Lifespan 设计

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 启动时：在事件循环内安装 MCP 插件
    try:
        await _get_mcp_plugin(app)
    except Exception:
        logger.warning("MCP 初始化失败，跳过 MCP 能力")
    yield
    # 关闭时：无需清理（MCPPlugin.deactivate 由调用方管理）
```

**为什么 MCP 安装放在 lifespan 而不是 create_app 中？**
- `create_app()` 是同步函数，但 MCP 建连需要事件循环
- FastAPI 的 lifespan 在事件循环内执行，是 async 的
- 无 enabled server 时 `install_mcp` 返回 None（no-op），失败不阻断启动

---

## 3. REST API 契约

所有 REST API 以 `/api` 为前缀，返回 JSON。这是**前端开发的重要依据**，字段命名不可随意变更。

### 3.1 配置管理

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/config` | 获取当前配置（api_key 脱敏） |
| PUT | `/api/config` | 合并更新配置并持久化 |

#### GET /api/config —— 配置读取（脱敏）

```python
def _config_to_json(config: NexusConfig) -> dict:
    # api_key 掩码规则：保留前 3 位 + **** + 后 4 位，如 "sk-****abcd"
    # 过短的 key 返回 "****"
    # MCP env 值同样脱敏
    return {
        "providers": {
            "openai": {
                "api_key": "sk-****abcd",  # 掩码值
                "has_api_key": True,        # 是否有 key（前端判断显示逻辑）
                "model": "gpt-4o-mini",
                "max_tokens": 4096,
                ...
            }
        },
        "default_provider": "openai",
        "agent": {"system_prompt": "...", "max_steps": 30},
        "tools": {"enabled": ["read_file", "write_file", ...]},
        "mcp_servers": {...},
        "stream": True,
    }
```

#### PUT /api/config —— 配置更新（热生效）

```python
async def put_config(request: Request) -> JSONResponse:
    data = await request.json()
    _merge_config_json(cfg, data)       # 合并请求体到配置
    save_config(cfg, path=...)          # 持久化到 YAML
    
    # 关键：重建 Agent 使新配置立即生效
    new_agent = await create_agent_async(cfg)
    request.app.state.config = cfg
    request.app.state.agent = new_agent
    await _get_mcp_plugin(request.app)  # 重建后重新安装 MCP
    
    return JSONResponse(_config_to_json(cfg))
```

**教学点 —— 为什么配置变更后要重建 Agent？**

早期版本只保存配置到文件，不重建 Agent，导致：
- 切换 provider/model 后，Agent 仍使用旧的 LLM 实例
- 用户在前端修改配置后没有实际效果

**解决方案**：PUT /api/config 成功后立即调用 `create_agent_async()` 重建 Agent 并更新 `app.state.agent`。WebSocket 连接通过 `current_agent` 属性动态获取最新 Agent，无需重新连接。

#### api_key 合并规则

```python
def _merge_config_json(config, data):
    new_key = prov.get("api_key")
    if new_key and new_key != current_mask:
        pc.api_key = new_key  # 传新值 → 更新
    # 传空值 / None / 与当前掩码一致 → 不修改（避免误覆盖）
```

前端编辑配置时，api_key 显示为掩码值。如果用户没有修改，回传的掩码值不会覆盖明文 key。

---

### 3.2 会话管理

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/sessions/{id}` | 会话详情（元信息 + messages） |
| DELETE | `/api/sessions/{id}` | 删除会话（同步删日志） |

---

### 3.3 工具管理

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/tools` | 已注册工具列表 |

```python
@app.get("/api/tools")
async def list_tools(request: Request):
    # 主路径：以 agent.tool_registry 为准（含 MCP 远端工具）
    registered = list(request.app.state.agent.tool_registry.list())
    
    # 返回格式区分 origin：
    # - builtin：内置工具（read_file, write_file, shell 等）
    # - mcp：MCP 工具（命名约定 mcp__{server}__{tool}）
    
    # 兜底：registry 为空时退回静态构造（保证前端始终能拿到工具清单）
```

**教学点**：为什么 registry 为空时还要兜底？
- 某些启动路径下 Agent 可能尚未完成工具注册
- 前端配置页面需要展示可用工具列表
- 兜底策略确保 API 契约稳定

---

### 3.4 MCP Server 管理

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/mcp` | MCP server 列表（配置 + 运行时状态合并） |
| POST | `/api/mcp` | 新增 MCP server（校验 → 持久化 → 热连接） |
| PUT | `/api/mcp/{name}` | 更新 MCP server（env 支持掩码回传） |
| DELETE | `/api/mcp/{name}` | 移除 MCP server（断开 + 注销工具 + 删配置） |
| POST | `/api/mcp/{name}/reconnect` | 重连刷新工具列表 |

#### MCP 状态合并逻辑

```python
async def _mcp_status_items(app) -> list[dict]:
    # 以 config.mcp_servers（配置字段 + env 掩码）为骨架
    # plugin 存在时：用 manager.get_status() 填充 status/error/tool_count/tools
    # plugin 不存在时：disabled → "disabled"，其余 → "disconnected"
```

---

## 4. WebSocket 聊天系统

### 4.1 连接模型

```
客户端 ──WebSocket──> /ws/chat
                        │
                        ▼
              ┌─────────────────┐
              │  _ChatConnection │  ← 每个连接独立维护一份 history
              │  - session_id    │  ← 8 位 uuid，贯穿连接生命周期
              │  - history       │  ← list[dict]，多轮上下文
              │  - _run_task     │  ← 并发保护：一次只允许一个 run
              └─────────────────┘
```

### 4.2 消息协议

**客户端 → 服务端**：

| type | 字段 | 说明 |
|------|------|------|
| `message` | `content: str` | 用户消息 |
| `reset` | - | 清空历史，生成新 session_id |
| `restore` | `messages: list`, `session_id: str` | 恢复指定会话 |
| `slash_command` | `command: str` | 斜杠命令 |

**服务端 → 客户端**：

| type | 字段 | 说明 |
|------|------|------|
| `thinking_delta` | `delta: str` | 思考链增量 |
| `content_delta` | `delta: str` | 回复内容增量 |
| `tool_call` | `name, args, result, error, success, index` | 工具调用结果 |
| `usage` | `prompt_tokens, completion_tokens, total_tokens` | Token 统计 |
| `done` | `steps, usage, session_id` | 执行完成 |
| `error` | `message: str` | 错误信息 |
| `slash_command_result` | `command, title, content` | 命令结果 |

### 4.3 _ChatConnection 实现详解

```python
class _ChatConnection:
    def __init__(self, ws, agent, session_manager, app):
        self.ws = ws
        self.agent = agent
        self.app = app  # 用于动态获取最新 agent
        self.session_id = str(uuid.uuid4())[:8]  # 8 位会话 ID
        self.history = []
        self._run_task = None
    
    @property
    def current_agent(self) -> Agent:
        # 配置变更后，从 app.state 获取最新 Agent
        if self.app is not None:
            return self.app.state.agent
        return self.agent
```

#### handle() —— 消息分发

```python
async def handle(self, data: dict) -> None:
    msg_type = data.get("type")
    
    if msg_type == "reset":
        self.history = []
        self.session_id = str(uuid.uuid4())[:8]  # 新会话
        await self.ws.send_json({"type": "reset_ok"})
    
    elif msg_type == "restore":
        self.history = data.get("messages", [])
        # 关键：用前端传来的 session_id 覆盖，使后续保存写到原文件
        if data.get("session_id"):
            self.session_id = data["session_id"]
        await self.ws.send_json({"type": "restore_ok", ...})
    
    elif msg_type == "message":
        if self.running:
            await self.ws.send_json({"type": "error", ...})
            return
        self._run_task = asyncio.create_task(self._run(content))
```

**教学点 —— restore 时为什么用前端传来的 session_id？**

这是**修复历史会话恢复的 bug** 后的设计：
- 早期版本：restore 只恢复 messages，但 session_id 仍用新的 → 新消息保存到新文件
- 修复后：restore 携带原始 session_id，后端覆盖 `self.session_id` → 新消息追加到原文件

#### _run() —— 执行与事件推送

```python
async def _run(self, content: str) -> None:
    tool_index = 0
    usage_acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    
    async def on_llm_chunk(event):
        # 推送到前端：thinking_delta / content_delta
        await send({"type": "thinking_delta", "delta": delta_reasoning})
        await send({"type": "content_delta", "delta": delta_content})
    
    async def on_after_tool(event):
        # 推送到前端：tool_call（result 截断 500 字符）
        nonlocal tool_index
        tool_index += 1
        await send({"type": "tool_call", "name": ..., "result": result_str[:500], ...})
    
    # 订阅事件 → 执行 → 取消订阅
    await events.subscribe(EventType.LLM_CHUNK, on_llm_chunk)
    try:
        state = await agent.run(content, initial_messages=self.history)
        self.history = [m for m in state.messages if m.get("role") != "system"]
        self._save_session()  # 覆盖式持久化
        await send({"type": "done", "session_id": self.session_id, ...})
    finally:
        await events.unsubscribe(...)
```

#### 并发保护

```python
@property
def running(self) -> bool:
    return self._run_task is not None and not self._run_task.done()
```

- run 在后台 task 中执行，接收循环保持活跃
- run 进行中再收到 message → 直接回错误 `"上一个任务仍在执行中"`
- 连接关闭时 `cancel()` 取消未完成的 run

---

### 4.4 斜杠命令系统

```python
async def _handle_slash_command(self, command: str) -> None:
    if command == "/tools":
        # 返回已注册工具列表
    elif command == "/clear":
        # 清空历史，生成新 session_id
    elif command == "/help":
        # 返回帮助文本
    elif command == "/sessions":
        # 返回历史会话列表（最多 20 条）
```

---

## 5. 静态资源托管

```python
project_root = Path(__file__).resolve().parents[2]
dist_dir = project_root / "desktop" / "dist"
if dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="desktop")
```

- 若 `desktop/dist` 存在，挂载到根路径（`html=True` 支持 SPA 路由回退）
- 这是 Desktop 应用的生产部署方式：`nexus serve` 同时充当 API 后端和静态文件服务器

---

## 6. 与 CLI 的对比

| 维度 | CLI (repl.py) | Server (app.py) |
|------|---------------|-----------------|
| 交互方式 | 终端输入 | WebSocket / HTTP |
| 显示层 | DisplayManager (Rich) | 前端 React 组件 |
| 事件订阅 | 本地回调 | WebSocket send_json |
| 会话保存 | 退出时 / `/save` | 每次 run 完成后 |
| 配置更新 | 命令行参数 | PUT /api/config |
| MCP 安装 | REPL 启动时 | Lifespan 启动时 |
| **共享逻辑** | **Agent.run()、EventBus、SessionManager、create_agent()** |

---

## 7. 依赖关系

```
nexus/server/app.py
    ├── fastapi (FastAPI, WebSocket, HTTPException, StaticFiles)
    ├── nexus.core.factory (create_agent, create_agent_async, register_tools, install_mcp)
    ├── nexus.cli.config (NexusConfig, load_config, save_config)
    ├── nexus.cli.session (SessionManager)
    ├── nexus.core.agent.agent (Agent)
    ├── nexus.core.event.event_types (EventType)
    ├── nexus.core.event.types (Event)
    ├── nexus.core.state.types (AgentState)
    └── nexus.llm.base (BaseLLM)
```
