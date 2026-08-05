# 05 - CLI 与交互系统

> 模块路径：`nexus/cli/`
> 教学视角：CLI 不是「附加功能」，而是 Nexus 的第一用户界面。理解 CLI 的设计哲学，就能理解整个框架「运行时与策略分离」原则在终端场景下的落地方式。

---

## 1. 模块定位与设计哲学

CLI（Command Line Interface）模块是 Nexus 面向开发者的**第一入口**。它的设计目标不是「能跑就行」，而是提供**类 ChatGPT 的终端对话体验**，同时保持框架核心 philosophy：

- **Runtime 与显示分离**：`Repl` 负责对话流程控制，`DisplayManager` 负责终端渲染，两者通过事件总线解耦
- **一次问答 vs 持续对话**：支持 `nexus "prompt"` 单次执行和 `nexus` REPL 交互两种模式
- **会话持久化**：对话历史自动保存到 `~/.nexus/sessions/`，支持跨进程恢复

### CLI 的三种使用模式

```
nexus "写一个快速排序"      # 单次执行模式（single run）
nexus                       # 进入 REPL 交互模式
nexus --continue            # 恢复最近会话继续对话
nexus sessions list         # 管理历史会话
nexus serve                 # 启动 HTTP + WebSocket 服务
```

---

## 2. 核心组件拆解

### 2.1 main.py —— CLI 统一入口与命令派发

[`nexus/cli/main.py`](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/main.py) 是整个 CLI 的「交通枢纽」，承担以下职责：

#### 命令行参数解析

```python
parser = argparse.ArgumentParser(prog="nexus", description="Nexus CLI Agent")
parser.add_argument("prompt", nargs="?", help="直接执行的任务描述")
parser.add_argument("--model", help="LLM 模型名称")
parser.add_argument("--provider", help="LLM Provider (openai|anthropic|minimax)")
parser.add_argument("--continue", dest="continue_session", action="store_true")
# ... 更多参数
```

**设计要点**：
- `prompt` 是位置参数且 `nargs="?"`：传了就走单次模式，不传进入 REPL
- 所有 `--` 参数都映射到 `NexusConfig` 的对应字段，优先级最高（覆盖配置文件）

#### 配置加载与 Agent 创建

```python
# 五级配置合并：默认值 → 用户级 YAML → 项目级 YAML → 环境变量 → 命令行参数
config = load_config(cli_args, work_dir=work_dir)

# 通过核心工厂创建 Agent（与 Server 共享同一组装逻辑）
from nexus.core.factory import create_agent
agent = create_agent(config)
```

这里体现了**关键架构决策**：CLI 不直接构造 Agent，而是调用 `nexus.core.factory.create_agent()`，确保 CLI 和 Server 的 Agent 组装逻辑完全一致。

#### 模式派发

```python
if args.continue_session:
    _run_continue(agent, config, run_id=run_id, log_file=log_file)
elif args.prompt:
    _run_single(agent, config, args.prompt)   # 单次执行
else:
    repl = Repl(agent=agent, display=display, config=config, ...)
    asyncio.run(repl.run())                   # REPL 交互
```

#### 日志配置 setup_logging()

```python
def setup_logging(verbose=False, debug=False, session_id=None, log_dir=None) -> str:
    # 终端日志级别：debug > verbose > 默认(warning)
    # 文件日志始终 DEBUG 级别，按会话命名存储在 ~/.nexus/logs/<session_id>.log
    # 与 ~/.nexus/sessions/<session_id>.json 一一对应
```

**教学点**：为什么日志文件和会话 JSON 要一一对应？
- 删除会话时可以同步清理日志
- 排查问题时可以按会话 ID 快速定位对应日志

---

### 2.2 repl.py —— 交互式对话引擎

[`nexus/cli/repl.py`](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/repl.py) 封装了 REPL（Read-Eval-Print Loop）的完整流程，是 CLI 最核心的交互组件。

#### Repl 类架构

```
┌─────────────────────────────────────────┐
│              Repl 类                     │
├─────────────────────────────────────────┤
│  agent: Agent          # 执行核心        │
│  display: DisplayManager  # 显示层       │
│  config: NexusConfig   # 配置            │
│  session_mgr: SessionManager  # 会话管理 │
│  _conversation_history: list[dict]      │
│  _session: PromptSession  # prompt_toolkit│
└─────────────────────────────────────────┘
```

#### 主循环 run()

```python
async def run(self) -> None:
    self.display.show_welcome()
    await self._install_mcp()  # 启动时加载 MCP 插件
    
    while self._running:
        user_input = await self._session.prompt_async([("class:prompt", "❯ ")])
        
        if user_input.startswith("/"):
            await self._handle_command(user_input)  # 内置命令
        else:
            await self._execute_task(user_input)      # 提交给 Agent
```

#### 任务执行 _execute_task() —— 事件驱动的流式展示

这是整个 REPL **最精华的实现**，展示了如何**通过 EventBus 将 Agent 的执行过程实时渲染到终端**：

```python
async def _execute_task(self, user_input: str) -> None:
    # 1. 渲染用户消息 → 分隔线 → AI 标签（角色化对话流）
    self.display.render_user_message(user_input)
    self.display.render_divider()
    self.display.render_assistant_header()
    
    # 2. 定义事件处理器（纯 append 模式，滚动安全）
    async def on_llm_chunk(event: Event) -> None:
        delta_reasoning = event.payload.get("delta_reasoning", "")
        delta_content = event.payload.get("delta_content", "")
        if delta_reasoning:
            self.display.print_thinking_chunk(delta_reasoning)
        if delta_content:
            self.display.print_response_chunk(delta_content)
    
    async def on_after_tool(event: Event) -> None:
        self.display.render_tool_call(
            tool_name=event.payload["tool_name"],
            args=event.payload["args"],
            result=str(event.payload.get("result", ""))[:200],
            success=event.payload.get("error") is None,
        )
    
    # 3. 订阅事件 → 执行 → 取消订阅（避免泄露）
    await self.agent.events.subscribe(EventType.LLM_CHUNK, on_llm_chunk)
    await self.agent.events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)
    try:
        state = await self.agent.run(user_input, initial_messages=self._conversation_history)
        # 用 state.messages 刷新跨轮历史（保留 tool 消息）
        self._conversation_history = [msg for msg in state.messages if msg.get("role") != "system"]
    finally:
        await self.agent.events.unsubscribe(...)
```

**教学点 —— 为什么采用纯 append 模式而不是 Rich Live？**

早期版本使用 `rich.live.Live` 进行流式渲染，但遇到了严重的终端滚动问题：
- Live 组件需要光标控制序列来定位刷新区域
- 用户滚动终端后，光标定位错乱，导致内容重复、残留、错位
- **解决方案**：改用纯 append 模式（`print_thinking_chunk` / `print_response_chunk`）
  - 思考链：行首加 `│ ` gutter，增量 print，无光标控制序列
  - 回复：段落缓冲（遇到 `\n\n` 或代码块闭合才渲染 Markdown）
  - 结果：滚动绝对安全，代价是无法"更新"已输出内容（设计上的合理取舍）

#### 内置命令系统

| 命令 | 功能 | 实现要点 |
|------|------|----------|
| `/clear` | 清空对话上下文 | 重新创建 Agent（保留 LLM 和 Policy），重新注册工具 |
| `/save` | 手动保存会话 | 调用 `SessionManager.save()`，传入 `run_id` 实现覆盖式保存 |
| `/tools` | 列出已注册工具 | 渲染 Rich Table，区分内置工具和 MCP 工具 |
| `/mcp` | 管理 MCP server | 支持 list/tools/add/remove/enable/disable/reconnect 子命令 |
| `/quit`, `/exit` | 退出 REPL | 设置 `_running = False`，触发退出流程 |
| `/help` | 显示帮助 | 渲染 Rich Panel（命令表 + 快捷键表） |

**`/mcp` 命令族的热生效机制**：

```python
async def _handle_mcp_command(self, args: str) -> None:
    # add / enable / disable：修改 config.mcp_servers → save_config 持久化 → _reload_or_install_mcp 热重载
    # remove：manager.remove() 断开并注销工具 → 从配置移除 → 持久化
    # reconnect：manager.reconnect() 单点重连
```

这是 **"配置即代码"** 理念的实践：用户在 REPL 中的修改立即持久化到 `~/.nexus/nexus.yaml`，下次启动自动生效。

#### 会话恢复 restore_session()

```python
async def restore_session(self, session: AgentState | dict) -> None:
    if isinstance(session, AgentState):
        self._conversation_history = list(session.messages)
    elif isinstance(session, dict):
        # 从 dict 格式提取 messages
        messages = session.get("state", {}).get("messages", [])
        self._conversation_history = messages
```

**教学点**：为什么跨轮对话历史在 REPL 层维护，而不是 Agent 层？
- Agent.run() 每次创建新的 AgentState（状态隔离原则）
- REPL 作为「用户交互层」，负责维护跨轮上下文
- 这样设计使得 Agent 更纯粹（无状态），REPL 更灵活（可清空、可恢复）

---

### 2.3 display.py —— 终端渲染引擎

[`nexus/cli/display.py`](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/display.py) 基于 **Rich** 库封装了所有终端输出逻辑，是 CLI 的「视图层」。

#### 核心设计：段落缓冲 + 代码块保护

```python
def print_response_chunk(self, chunk: str) -> None:
    """按段落渲染 Markdown（滚动安全）。"""
    self._response_buffer += chunk
    
    # 代码块保护：未闭合的 ``` 暂不分段
    if self._response_buffer.count("```") % 2 == 1:
        return
    
    # 按 \n\n 拆分段落：完整段落渲染 Markdown，未完成的保留在 buffer
    while "\n\n" in self._response_buffer:
        para, self._response_buffer = self._response_buffer.split("\n\n", 1)
        self.console.print(Markdown(para), end="\n\n", soft_wrap=True)
```

**为什么这样设计？**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 纯 append 原始文本 | 滚动绝对安全 | Markdown 语法裸露，不美观 |
| Rich Live 逐字刷新 | 流畅 | 光标控制序列导致滚动错乱 |
| **段落缓冲（当前方案）** | 完整段落 Markdown 渲染 + 滚动安全 | 延迟一个段落（可接受） |

#### 工具调用渲染 render_tool_call()

```python
def render_tool_call(self, tool_name, args, result, success, index):
    # 紧凑单行式 Panel：
    # title: 🔧 [n] tool_name（失败时 ❌）
    # 内容: dim 参数摘要 + ✅/❌ 结果
    # 边框: cyan（成功）/ red（失败）
```

#### MCP Server 状态渲染

```python
def render_mcp_servers(self, status_list):
    # Rich Table：名称 / 类型 / 启用 / 状态（配颜色）/ 工具数
    # connected=绿色, error=红色, disabled=灰色, 其余=黄色
```

---

### 2.4 session.py —— 会话持久化管理

[`nexus/cli/session.py`](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/session.py) 负责对话历史的 CRUD 操作。

#### SessionManager 核心 API

```python
class SessionManager:
    def save(self, state, metadata=None, auto_truncate=False, session_id=None, log_file=None) -> str:
        """保存会话到 ~/.nexus/sessions/<id>.json"""
        
    def load(self, session_id: str) -> AgentState | None:
        """加载指定会话"""
        
    def load_latest(self) -> tuple[str | None, AgentState | None]:
        """加载最近会话（按文件修改时间排序）"""
        
    def list_sessions(self) -> list[dict]:
        """列出历史会话（最多 20 条，按时间倒序）"""
        
    def delete(self, session_id: str, delete_logs=True) -> bool:
        """删除会话（同步清理关联日志）"""
```

#### 会话文件格式

```json
{
  "session_id": "a1b2c3d4",
  "version": "1.0",
  "created_at": "2026-08-05T12:00:00+00:00",
  "summary": "写一个快速排序...",
  "metadata": {"mode": "repl", "log_file": "/Users/xxx/.nexus/logs/a1b2c3d4.log"},
  "state": {
    "task": "repl session",
    "messages": [...],
    "steps": [],
    "tool_calls": [],
    ...
  }
}
```

#### 自动截断策略

```python
def _truncate_state(self, state: AgentState) -> AgentState:
    # 保留所有 system 消息
    # 非 system 消息保留最近 50 轮（100 条）
    # 返回新实例，不修改原始 state
```

**重要变更**：早期版本默认启用 `auto_truncate=True`，但后来发现截断会导致 assistant 的 `tool_calls` 失去对应的 `tool` result，造成上下文断裂。现在默认 `auto_truncate=False`，保留全量对话。

---

## 3. CLI 子命令体系

### 3.1 `nexus serve` / `nexus ui`

启动 FastAPI 服务，是 Server 模块的 CLI 入口：

```python
def _run_server_command(command, argv):
    # nexus serve [--port N] [--host H] → 启动 HTTP + WebSocket 服务
    # nexus ui [--port N] [--host H] → 启动服务 + 自动打开浏览器
    app = create_app(work_dir=os.getcwd())
    uvicorn.run(app, host=args.host, port=args.port)
```

### 3.2 `nexus sessions`

会话管理子命令：

```bash
nexus sessions list              # 列出历史会话（默认行为）
nexus sessions delete <id>       # 删除指定会话（带二次确认）
nexus sessions restore <id>      # 恢复指定会话并启动 REPL
```

---

## 4. 关键设计决策解析

### 决策 1：为什么使用 prompt_toolkit 而非自建输入循环？

- **FileHistory**：跨进程命令历史持久化（`~/.nexus/repl_history`）
- **快捷键绑定**：Ctrl+D 退出、Esc+Enter 换行、Ctrl+C 中断任务
- **自动补全**：输入 `/` 后 Tab 补全内置命令
- **异步兼容**：`prompt_async()` 与 asyncio 事件循环协作

### 决策 2：为什么 thinking 和 response 都用纯 append 模式？

这是**从错误中学习**的设计：

1. **尝试 1**：Rich Live 逐字刷新 → 滚动时光标定位错乱
2. **尝试 2**：Status spinner + 一次性渲染 → 无法展示 thinking 内容
3. **最终方案**：
   - thinking：`print_thinking_chunk()` 行首加 `│ ` gutter，增量 print
   - response：`print_response_chunk()` 段落缓冲，滚动安全

### 决策 3：为什么日志按会话隔离？

```
~/.nexus/
├── sessions/
│   ├── a1b2c3d4.json      # 会话数据
│   └── e5f6g7h8.json
├── logs/
│   ├── a1b2c3d4.log       # 对应会话的日志（一一对应）
│   └── e5f6g7h8.log
└── nexus.yaml             # 用户级配置
```

- 会话删除时同步清理日志
- 按会话排查问题（`tail -f ~/.nexus/logs/<id>.log`）

---

## 5. 扩展指南

### 添加新的 REPL 内置命令

1. 在 `_get_completer()` 中添加命令名
2. 在 `_handle_command()` 中添加分支处理
3. 在 `render_help_panel()` 中添加帮助文本

### 自定义显示样式

`DisplayManager` 的所有渲染方法都是独立的，可以：
- 继承 `DisplayManager` 覆写特定方法
- 替换 `Console` 实例改变输出目标（如重定向到文件）

---

## 6. 依赖关系

```
nexus/cli/main.py
    ├── nexus.core.factory (create_agent, register_tools, install_mcp)
    ├── nexus.cli.config (NexusConfig, load_config, save_config)
    ├── nexus.cli.repl (Repl)
    ├── nexus.cli.display (DisplayManager)
    ├── nexus.cli.session (SessionManager)
    └── nexus.logging

nexus/cli/repl.py
    ├── nexus.core.agent.agent (Agent)
    ├── nexus.core.event.event_types (EventType)
    ├── nexus.core.state.types (AgentState)
    ├── nexus.cli.display (DisplayManager)
    ├── nexus.cli.session (SessionManager)
    └── prompt_toolkit

nexus/cli/display.py
    └── rich (Console, Live, Markdown, Panel, Table, Spinner, Text)

nexus/cli/session.py
    ├── nexus.core.state.types (AgentState)
    └── nexus.logging
```
