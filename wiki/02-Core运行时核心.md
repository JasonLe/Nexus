# Core 运行时核心详解

Core 是 Nexus 框架的心脏，包含 Agent 执行的所有基础能力。本章节深入讲解每个核心模块的设计思路、实现细节和使用方法。

---

## 1. Agent 门面类 (`nexus/core/agent/agent.py`)

### 1.1 设计定位

Agent 是**用户交互的唯一入口**（Facade 模式）。它封装了 Runtime / ToolRegistry / PluginRegistry / EventBus，提供简洁的配置+执行 API。

用户不需要直接操作 Runtime 或 Policy 细节，通过 Agent 完成：
- 配置 LLM → 注册工具 → 安装插件 → 订阅事件 → 执行任务

### 1.2 类定义

```python
class Agent:
    def __init__(
        self,
        llm: BaseLLM,
        policy: ExecutionPolicy | None = None,
        system_prompt: str | None = None,
        max_steps: int = 30,
        name: str = "nexus",
        stream: bool = True,
    ) -> None
```

### 1.3 关键属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `runtime` | `Runtime` | 底层运行时引擎 |
| `llm` | `BaseLLM` | LLM 实例 |
| `policy` | `ExecutionPolicy` | 执行策略，默认 `ReActPolicy` |
| `system_prompt` | `str \| None` | 系统提示词，每次 run 时注入 |
| `max_steps` | `int` | 最大执行步数 |
| `tool_registry` | `ToolRegistry` | 快捷引用：直接操作工具注册 |
| `events` | `EventBus` | 快捷引用：订阅/发布事件 |
| `plugin_registry` | `PluginRegistry` | 快捷引用：管理插件生命周期 |

### 1.4 核心方法

#### `register_tool(tool: BaseTool) -> None`

将工具注册到内部 ToolRegistry。LLM 可通过 function calling 自动发现。

#### `async install(plugin: Plugin) -> None`

安装插件的完整流程：
1. 调用 `plugin.install(self)` —— 声明式注册（订阅事件、注册工具）
2. 调用 `self.plugin_registry.register(plugin)` —— 触发 `activate()`

#### `async run(task, variables, initial_messages) -> AgentState`

Agent 的主入口方法。执行流程：
1. 构建 messages：`system_prompt` + `initial_messages`
2. 将 `_stream` 标志注入 variables
3. 委托给 `Runtime.run()` 完成完整执行
4. 返回最终 `AgentState`

---

## 2. Runtime 调度引擎 (`nexus/core/runtime/runtime.py`)

### 2.1 设计定位

Runtime 是 Agent 的**执行中枢**，负责：
1. **生命周期管理** —— 创建 ExecutionContext、初始化 AgentState、清理资源
2. **调度循环** —— Policy 驱动：`Policy.next_action → Runtime.execute`
3. **事件派发** —— 在关键生命周期节点派发 8 类 Events
4. **状态管理** —— 每次 Action 执行后更新 AgentState
5. **错误处理** —— 捕获异常、派发 OnError、决定是否继续

### 2.2 调度模型

```
Runtime.run()
  │
  ├── 1. 创建 AgentState
  ├── 2. 写入 initial_messages
  ├── 3. 追加 task 为 user 消息
  ├── 4. 创建 ExecutionContext
  ├── 5. 派发 BEFORE_AGENT_RUN
  │
  ├── 6. 进入调度循环
  │     while True:
  │       a. action = await policy.next_action(context)
  │       b. 按 Action 类型分发执行
  │       c. 更新 State
  │       d. 派发 AFTER 事件
  │       e. 检查 FinishAction / ErrorAction → break
  │       f. state.current_step += 1
  │
  ├── 7. 派发 AFTER_AGENT_RUN
  └── 8. 返回 AgentState
```

### 2.3 Action 执行方法

#### `_execute_llm_call(context, action)`

执行 LLM 调用 Action：
1. 派发 `BEFORE_LLM_CALL`
2. 调用 LLM（流式或非流式）
3. 将 assistant 消息添加到 `state.messages`
4. 将 `_last_llm_response` 存入 `state.variables`（供 Policy 读取 tool_calls）
5. 创建 Step 记录
6. 派发 `AFTER_LLM_CALL`

**流式路径的特殊处理：**

```python
stream_mode = context.variables.get("_stream", True)
if stream_mode and hasattr(context.llm, "stream_chat"):
    # 流式路径：聚合 chunks + 派发 LLM_CHUNK 事件
    async for chunk in context.llm.stream_chat(...):
        content_parts.append(chunk.delta_content)
        reasoning_parts.append(chunk.delta_reasoning)
        # 派发 LLM_CHUNK 事件（供前端/UI 实时显示）
        await self._event_bus.publish(Event(
            type=EventType.LLM_CHUNK,
            payload={"delta_content": ..., "delta_reasoning": ...},
        ))
```

#### `_execute_tool_call(context, action)`

执行工具调用 Action：
1. 派发 `BEFORE_TOOL_CALL`
2. 调用 `ToolExecutor.execute()`
3. 创建 `ToolCallRecord` 并添加到 `state.tool_calls`
4. 将 tool result 消息追加到 `state.messages`
5. 更新 `state.variables["_last_tool_result"]`
6. 创建 Step 记录
7. 派发 `AFTER_TOOL_CALL`

#### `_handle_finish(context, action)` / `_handle_error(context, action)`

处理终止 Action：
- `FinishAction`：写入 `state.intermediate_results`，派发 `ON_FINISH`
- `ErrorAction`：写入错误信息，派发 `ON_ERROR`

---

## 3. ExecutionPolicy 抽象 (`nexus/core/executor/policy.py`)

### 3.1 设计定位

框架**最核心的扩展点**。将"下一步做什么"的决策权从 Runtime 中剥离出来。

不同的 Policy 实现代表不同的 Agent 模式：
- **ReActPolicy**：交替进行 LLM 思考和 Tool 调用（默认）
- **PlanAndExecutePolicy**：先规划再逐步执行
- **ReflectionPolicy**：执行后反思并改进

### 3.2 接口定义

```python
class ExecutionPolicy(ABC):
    @abstractmethod
    async def next_action(self, context: ExecutionContext) -> Action:
        """根据当前上下文决定下一步 Action。
        
        实现约定：
        - 应是无副作用的纯决策函数（只读 context，不修改 state）
        - 返回的 Action 由 Runtime 执行并产生副作用
        - 若需终止，返回 FinishAction 或 ErrorAction
        """
```

---

## 4. ReActPolicy 实现 (`nexus/core/executor/react_policy.py`)

### 4.1 状态转移图

```
    START
      │
      ▼
  ┌─────────────┐    无 tool_calls    ┌──────────────┐
  │ LLMCallAction│ ──────────────────→ │ FinishAction  │
  └──────┬──────┘                     └──────────────┘
         │ 有 tool_calls
         ▼
  ┌──────────────┐                     ┌─────────────┐
  │ToolCallAction │ ─── 结果回写后 ──→ │ LLMCallAction│（循环）
  └──────────────┘                     └─────────────┘

  max_steps 超限 → ErrorAction
```

### 4.2 决策逻辑

```python
async def next_action(self, context):
    steps = context.state.steps
    
    # 首次调用：无历史步骤，直接发起 LLM 调用
    if not steps:
        return LLMCallAction(messages=list(state.messages), tools=self._get_tool_schemas(context))
    
    last_step_type = steps[-1].step_type
    
    # LLM 刚返回：检查是否包含 tool_calls
    if last_step_type == "llm_call":
        return self._handle_after_llm(context)
    
    # 工具刚执行完：检查是否还有待执行工具，或回到 LLM
    if last_step_type == "tool_call":
        return self._handle_after_tool(context)
```

### 4.3 关键内部状态

| 属性 | 说明 |
|------|------|
| `_step_count` | 已执行的 LLM 调用步数（用于 max_steps 控制）|
| `_pending_tool_calls` | 缓存的本轮待执行 tool_calls 队列（LLM 可能一次返回多个）|

### 4.4 为什么 tool_calls 要缓存到 `_pending_tool_calls`？

OpenAI 等模型**一次可以返回多个 tool_calls**。Nexus 的设计是**串行执行**（逐一弹出执行），而非并行。原因：
1. 工具之间可能有依赖关系（后一个工具需要前一个的结果）
2. 简化状态管理，避免并发竞态
3. 大多数场景下串行已足够

---

## 5. Action 类型族 (`nexus/core/executor/actions.py`)

| Action | 含义 | Runtime 行为 |
|--------|------|-------------|
| `LLMCallAction` | 调用 LLM | `stream_chat()` 或 `chat()`，结果写回 state |
| `ToolCallAction` | 调用工具 | `ToolExecutor.execute()`，结果写回 conversation |
| `PlanAction` | 进入规划 | 驱动 LLM 将任务拆解为步骤 |
| `ReflectAction` | 进入反思 | 驱动 LLM 审视已执行步骤 |
| `FinishAction` | 正常终止 | 记录结果，退出循环 |
| `ErrorAction` | 异常终止 | 记录错误，退出循环 |

所有 Action 都是纯数据类（`@dataclass`），不含任何执行逻辑。

---

## 6. AgentState 运行时状态 (`nexus/core/state/types.py`)

### 6.1 设计定位

State 是 Agent 执行的**唯一真实来源（Single Source of Truth）**：
- Runtime **只修改** State
- Policy **只读取** State 做决策
- 数据流单向：Runtime → State → Policy

### 6.2 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `task` | `str` | 当前任务描述 |
| `steps` | `list[Step]` | 历史执行步骤 |
| `tool_calls` | `list[ToolCallRecord]` | 全局工具调用记录（展平视图）|
| `messages` | `list[dict]` | 对话消息历史（OpenAI API 格式）|
| `variables` | `dict[str, Any]` | 运行时变量，Policy 可读写 |
| `intermediate_results` | `dict[str, Any]` | 中间结果（final_result, finish_message, error）|
| `run_id` | `str` | 本次运行唯一标识（uuid4）|
| `current_step` | `int` | 当前执行步骤计数 |
| `created_at` | `datetime` | State 创建时间 |

### 6.3 Step 数据结构

```python
@dataclass
class Step:
    step_id: str          # uuid4
    step_type: str        # "llm_call" | "tool_call" | "plan" | "reflect"
    input_messages: list  # 发送给 LLM 的消息快照
    output_content: str   # LLM 输出的文本内容
    tool_calls: list      # 该步内发生的工具调用记录
    timestamp: datetime
    duration_ms: float
```

### 6.4 序列化与反序列化

`AgentState.serialize()` 返回 JSON-able dict，支持嵌套 Step / ToolCallRecord 的递归序列化。`AgentState.deserialize()` 从 dict 恢复，支持缺失字段回退（向后兼容）。

---

## 7. ExecutionContext 请求级 DI 容器 (`nexus/core/context/context.py`)

### 7.1 设计定位

将单次 Agent run 所需的所有组件聚合在一起，避免 Runtime/Policy 构造函数逐一注入大量参数。

### 7.2 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `state` | `AgentState` | 运行时状态 |
| `llm` | `Any` | BaseLLM 实例（避免循环导入用 Any）|
| `tool_executor` | `ToolExecutor` | 工具执行器 |
| `events` | `EventBus` | 事件总线 |
| `max_steps` | `int` | 最大步数上限 |
| `variables` | `dict[str, Any]` | 运行时变量（与 state.variables 共享引用）|
| `memory` | `Any` | BaseMemory 实例（预留）|

---

## 8. EventBus 事件总线 (`nexus/core/event/event_bus.py`)

### 8.1 设计定位

Async 发布/订阅模式的事件分发中枢。覆盖 Agent 执行全生命周期。

### 8.2 核心设计特点

1. **Async 优先**：所有 handler 通过 `asyncio.TaskGroup` 并发执行
2. **异常隔离**：单个 handler 抛出异常不影响其他 handler 或主流程
3. **支持同步/异步 handler**：同步函数直接调用，异步函数 await

### 8.3 事件类型 (`nexus/core/event/event_types.py`)

| 事件 | 触发时机 | payload 关键字段 |
|------|---------|-----------------|
| `BEFORE_AGENT_RUN` | Agent 执行开始前 | agent_name, session_id, run_id |
| `AFTER_AGENT_RUN` | Agent 执行完成后 | agent_name, result, run_id |
| `BEFORE_LLM_CALL` | LLM 调用前 | model, provider, messages, tools |
| `LLM_CHUNK` | 流式 chunk 到达 | delta_content, delta_reasoning, delta_tool_calls |
| `AFTER_LLM_CALL` | LLM 调用后 | model, provider, response, usage |
| `BEFORE_TOOL_CALL` | 工具调用前 | tool_name, tool_call_id, args |
| `AFTER_TOOL_CALL` | 工具调用后 | tool_name, tool_call_id, result, error |
| `ON_ERROR` | 运行时异常 | error, traceback, recoverable, run_id |
| `ON_FINISH` | 运行时完全结束 | run_id, final_state |

### 8.4 典型执行顺序

```
BEFORE_AGENT_RUN
  → BEFORE_LLM_CALL → LLM_CHUNK* → AFTER_LLM_CALL
  → BEFORE_TOOL_CALL → AFTER_TOOL_CALL
  → [循环...]
  → ON_FINISH
  → AFTER_AGENT_RUN
```

---

## 9. 异常体系 (`nexus/core/exceptions/__init__.py`)

按**故障域**分层，使调用方可以按层级精确捕获：

```
NexusError (根)
  ├── LLMError
  │     ├── LLMRetryableError
  │     │     ├── LLMRateLimitError (429)
  │     │     ├── LLMTimeoutError
  │     │     └── LLMServerError (5xx)
  │     └── LLMAuthError (401/403)
  ├── ToolError
  │     ├── ToolValidationError
  │     └── ToolNotFoundError
  ├── StateError
  ├── PolicyError
  ├── AgentRuntimeError
  ├── PluginError
  └── EventError
```

---

## 10. Agent 组装工厂 (`nexus/core/factory.py`)

### 10.1 设计定位

将原本散落在 `cli/main.py` 中的 Agent 组装逻辑收敛到核心层，提供单一入口。

### 10.2 公开接口

| 函数 | 说明 |
|------|------|
| `create_llm(config)` | 根据 `default_provider` 创建对应 LLM 实例 |
| `register_tools(agent, config)` | 根据 `tools.enabled` 注册内置工具 |
| `create_agent(config)` | 同步入口：create_llm + Agent() + register_tools |
| `install_mcp(agent, config)` | 按 `mcp_servers` 安装 MCP 插件（async）|
| `create_agent_async(config)` | 异步入口：create_agent + install_mcp |

### 10.3 工具注册规则

- `enabled` 为空列表 → 注册**所有**内置工具（默认行为）
- `enabled` 为 `['__none__']` → **不注册任何工具**（全部禁用）
- 否则仅注册 `enabled` 中列出的工具
