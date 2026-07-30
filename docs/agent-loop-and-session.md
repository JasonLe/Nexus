# Nexus Agent 架构文档

本文档梳理 Nexus 的 Agent 执行循环（Agent Loop）、对话逻辑、会话保持机制与工具调用流程，作为理解与二次开发的参考。

---

## 目录

- [一、分层架构总览](#一分层架构总览)
- [二、Agent Loop 执行循环](#二agent-loop-执行循环)
- [三、对话逻辑与消息组织](#三对话逻辑与消息组织)
- [四、会话保持逻辑](#四会话保持逻辑)
- [五、工具调用逻辑](#五工具调用逻辑)
- [六、事件系统](#六事件系统)
- [七、关键设计观察](#七关键设计观察)

---

## 一、分层架构总览

系统采用"门面 + 调度引擎 + 策略 + 状态"四层分离设计：

| 层 | 文件 | 职责 |
|---|---|---|
| 门面层 | [agent.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/agent/agent.py) | 用户入口，组装 Runtime/LLM/Policy，注入 system prompt |
| 调度引擎 | [runtime.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py) | 驱动循环、派发事件、维护 State |
| 策略层 | [react_policy.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/executor/react_policy.py) | 决定下一步 Action（纯决策、无副作用） |
| 状态层 | [types.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/state/types.py) | AgentState 作为唯一真实来源 |
| 上下文 | [context.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/context/context.py) | 请求级 DI 容器，聚合 state/llm/tool_executor/events |
| 工具层 | [executor.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/executor.py) / [registry.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/registry.py) / [base.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/base.py) | 工具定义、注册、校验+执行+记录 |
| 持久化 | [session.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/session.py) / [repl.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/repl.py) | 会话保存/恢复 + REPL 跨轮历史 |

核心数据流是单向的：**Runtime → State → Policy**。Runtime 只改 State，Policy 只读 State 做决策，两者解耦，便于替换策略或引入新执行模型。

---

## 二、Agent Loop 执行循环

### 2.1 入口：Agent.run()

[agent.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/agent/agent.py) 是 Facade，`run()` 方法本身不执行循环，只做两件事：

1. **组装 initial_messages**（`agent.py:223-228`）：
   - 若配置了 `self.system_prompt`，先注入一条 `{"role": "system", "content": ...}`
   - 再 extend 调用方传入的 `initial_messages`（即 REPL 的跨轮历史）
2. **委托给 Runtime.run()**（`agent.py:238-245`）：传入 `task / llm / policy / initial_messages / variables / max_steps`，返回最终 `AgentState`。

构造时（`agent.py:113-123`）：
- `self.runtime = Runtime()` —— 内部已创建 `EventBus` / `ToolRegistry` / `ToolExecutor` / `PluginRegistry`
- `self.policy = policy or ReActPolicy(max_steps)` —— 默认 ReAct
- 通过属性代理暴露 `tool_registry` / `events` / `plugin_registry`

关键设计决策：system prompt 在每次 `run()` 自动注入为第一条 system 消息，保证 LLM persona 一致。

### 2.2 调度引擎：Runtime.run() 主循环

文件：[runtime.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py)（`run()` 在 `runtime.py:104-250`）

#### 启动阶段（`runtime.py:140-183`）

1. 创建 `AgentState(task=task)`（`runtime.py:141`）
2. 写入 `initial_messages` —— 循环调用 `state.add_message(role, content)`（`runtime.py:146-148`）
3. **将当前 task 追加为最后一条 user 消息**（`runtime.py:153`）：`state.add_message("user", task)`。这是必要的，否则 LLM 看不到最新问题
4. 创建 `ExecutionContext`（`runtime.py:156-163`），把 state/llm/tool_executor/events/max_steps/variables 聚合
5. 派发 `BEFORE_AGENT_RUN` 事件（`runtime.py:174-183`）

#### 调度循环（`runtime.py:186-216`）

循环条件是 `while True`，**循环次数由 Policy 返回的 Action 类型决定**：

```python
while True:
    action = await policy.next_action(context)      # 决策
    if isinstance(action, LLMCallAction):
        await self._execute_llm_call(context, action)
    elif isinstance(action, ToolCallAction):
        await self._execute_tool_call(context, action)
    elif isinstance(action, FinishAction):
        await self._handle_finish(context, action); break   # 跳出
    elif isinstance(action, ErrorAction):
        await self._handle_error(context, action); break    # 跳出
    else: # 未知类型兜底 → ErrorAction
        ...
    state.current_step += 1   # 步数递增
```

外层 `try/except`（`runtime.py:186, 218-227`）兜底所有未预期异常，转为 `ErrorAction` 走 `_handle_error`。

#### 终止阶段（`runtime.py:229-250`）

- 派发 `AFTER_AGENT_RUN` 事件
- 返回最终 `state`

> 注意：`current_step` 在每个 Action 执行完后递增（`runtime.py:216`），但 ReActPolicy 内部维护的 `_step_count` 是独立计数器（见 2.3）。

### 2.3 ReActPolicy 决策逻辑

文件：[react_policy.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/executor/react_policy.py)（`next_action` 在 `react_policy.py:75-122`）

Policy 通过 `context.state.steps[-1].step_type` 推断"上一个 Action 是什么"，分三种情况：

#### 情况 1：首次调用（`react_policy.py:94-105`）
- `steps` 为空 → `_step_count += 1`，返回 `LLMCallAction(messages=list(state.messages), tools=self._get_tool_schemas(context))`

#### 情况 2：上一步是 `llm_call`（`react_policy.py:110-111` → `_handle_after_llm`，`react_policy.py:128-170`）
读取 `state.messages[-1]`（刚追加的 assistant 消息）：
- **无 `tool_calls`** → LLM 已完成推理，返回 `FinishAction(result=assistant_msg["content"])`（`react_policy.py:139-141`）
- **有 `tool_calls`** → 解析每个 `tc["function"]["arguments"]`（兼容 JSON 字符串或 dict 两种格式，`react_policy.py:151-153`），缓存到 `self._pending_tool_calls` 队列，弹出第一个返回 `ToolCallAction`（`react_policy.py:159-170`）
- 解析失败 → 返回 `ErrorAction`（`react_policy.py:155-157`）

#### 情况 3：上一步是 `tool_call`（`react_policy.py:114-115` → `_handle_after_tool`，`react_policy.py:172-212`）
- `_pending_tool_calls` 非空 → 继续弹出下一个 `ToolCallAction`（同一批次多工具逐一执行，`react_policy.py:182-190`）
- 队列空 + `_step_count >= max_steps` → 返回 `ErrorAction("Max steps exceeded")`（`react_policy.py:193-199`）
- 队列空 + 未超限 → `_step_count += 1`，返回 `LLMCallAction` 把工具结果回馈给 LLM 继续推理（`react_policy.py:203-212`）

#### 关键设计点

- **`_step_count` vs `state.current_step` 独立**（`react_policy.py:60-63`）：前者是 Policy 内部状态，仅在进入 LLM 阶段时递增（工具调用不计入），用于 max_steps 决策；后者由 Runtime 在每个 Action 后递增。
- **同批次多 tool_calls 串行执行**：LLM 一次返回多个 tool_calls 时，通过 `_pending_tool_calls` 队列逐一弹出，全部执行完才回到 LLM。
- **Policy 是纯决策函数**（[policy.py:46-50](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/executor/policy.py)）：`next_action` 只读 context，不修改 state，副作用由 Runtime 执行 Action 时产生。

### 2.4 一次完整 ReAct 循环时序

```
用户 task "查北京天气"
   │
   ▼
[Runtime] 追加 user 消息 → state.messages
   │
   ▼
[Policy] next_action → LLMCallAction（首次）
   │
   ▼
[Runtime] _execute_llm_call
   - BEFORE_LLM_CALL 事件
   - llm.chat(messages, tools)
   - 追加 assistant 消息（含 tool_calls=[get_weather(city=Beijing)]）
   - AFTER_LLM_CALL 事件
   │
   ▼
[Policy] 上一步是 llm_call + 有 tool_calls
   - 解析 arguments，缓存到 _pending_tool_calls
   - 返回 ToolCallAction(get_weather, city=Beijing)
   │
   ▼
[Runtime] _execute_tool_call
   - BEFORE_TOOL_CALL 事件
   - tool_executor.execute → ToolResult("25°C, sunny")
   - 追加 tool 消息（role=tool, tool_call_id=...）
   - AFTER_TOOL_CALL 事件
   │
   ▼
[Policy] 上一步是 tool_call + 队列空
   - _step_count < max_steps → 返回 LLMCallAction
   │
   ▼
[Runtime] _execute_llm_call（第二轮）
   - LLM 看到工具结果，生成最终回复
   - 追加 assistant 消息（无 tool_calls）
   │
   ▼
[Policy] 上一步是 llm_call + 无 tool_calls
   - 返回 FinishAction(result="北京今天 25°C，晴天")
   │
   ▼
[Runtime] _handle_finish → AFTER_AGENT_RUN → 返回 state
```

---

## 三、对话逻辑与消息组织

### 3.1 messages 格式约定

`state.messages` 采用 **OpenAI Chat Completions 兼容格式**，统一在 [runtime.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py) 中写入：

| 消息类型 | 结构 | 写入位置 |
|---------|------|---------|
| system | `{"role": "system", "content": "..."}` | [agent.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/agent/agent.py) `run()` 注入 |
| user | `{"role": "user", "content": "..."}` | [runtime.py:153](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py) 追加当前 task |
| assistant（纯文本） | `{"role": "assistant", "content": "..."}` | [runtime.py:313-338](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py) |
| assistant（带工具调用） | `{"role": "assistant", "content": "思考...", "tool_calls": [...]}` | [runtime.py:313-338](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py) |
| tool（工具结果） | `{"role": "tool", "tool_call_id": "...", "content": "..."}` | [runtime.py:449-453](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py) |

### 3.2 assistant 消息的 tool_calls 字段

当 LLM 决定调用工具时，assistant 消息会包含 `tool_calls` 数组（`runtime.py:313-338`）：

```python
assistant_msg = {"role": "assistant"}
if response.content:
    assistant_msg["content"] = response.content      # 思考/推理文本
if response.tool_calls:
    assistant_msg["tool_calls"] = [
        {"id": tc.id, "type": "function",
         "function": {"name": tc.name,
                      "arguments": <str or json.dumps(tc.arguments)>}}
        for tc in response.tool_calls
    ]
state.messages.append(assistant_msg)
```

- `arguments` 是 JSON 字符串（OpenAI 规范），ReActPolicy 解析时兼容字符串或 dict 两种格式
- `content` 在有 tool_calls 时可能为空，也可能包含 LLM 的推理文本

### 3.3 tool 消息与 tool_call_id 配对

工具执行后，结果以 `role="tool"` 消息追加（`runtime.py:449-453`）：

```python
state.messages.append({
    "role": "tool",
    "tool_call_id": action.tool_call_id,   # 与 assistant.tool_calls[i].id 配对
    "content": str(result.data) if result.data is not None else result.error or "",
})
```

**配对关系**：`tool.tool_call_id` 必须等于某个 `assistant.tool_calls[i].id`，否则 API 会报 `tool id not found`（这也是之前 Anthropic provider 的 bug 根因）。

### 3.4 一次完整 ReAct 循环的 messages 演化

以"用户问北京天气 → LLM 调工具 → LLM 给答案"为例：

```
[
  {"role": "system",    "content": "<system_prompt>"},        # Agent.run 注入
  {"role": "user",      "content": "<历史 user>"},              # initial_messages（跨轮历史）
  {"role": "assistant", "content": "<历史 assistant>"},         # initial_messages
  {"role": "user",      "content": "查北京天气"},              # Runtime.run 追加

  # ---- 第一轮 LLM 调用后 ----
  {"role": "assistant", "content": "思考...",
   "tool_calls": [{"id":"call_1","type":"function",
                   "function":{"name":"get_weather",
                               "arguments":"{\"city\":\"Beijing\"}"}}]},

  # ---- 工具执行后 ----
  {"role": "tool", "tool_call_id": "call_1", "content": "25°C, sunny"},

  # ---- 第二轮 LLM 调用后（无 tool_calls → Finish）----
  {"role": "assistant", "content": "北京今天 25°C，晴天"}
]
```

### 3.5 AgentState 数据结构

文件：[types.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/state/types.py)（`AgentState` 在 `types.py:123-274`）

`AgentState` 是 dataclass，作为 Single Source of Truth：

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | str | 当前任务 |
| `steps` | list[Step] | 执行步骤轨迹（llm_call / tool_call） |
| `tool_calls` | list[ToolCallRecord] | 全局展平的工具调用记录 |
| `memory_refs` | list[str] | 外部 Memory 引用（预留） |
| `intermediate_results` | dict | 中间结果（`final_result` / `error` / `exception` / `traceback`） |
| `variables` | dict | 运行时变量（`_last_llm_response` / `_last_tool_result`） |
| `messages` | list[dict] | 对话历史（LLM API 格式） |
| `run_id` | str | UUID |
| `created_at` | datetime | 创建时间 |
| `current_step` | int | 步序号 |

`Step`（`types.py:69-120`）：`step_id` / `step_type`（`"llm_call"` | `"tool_call"` | `"plan"` | `"reflect"`）/ `input_messages`（发送给 LLM 的快照）/ `output_content` / `tool_calls` / `timestamp` / `duration_ms`

**完整的 serialize/deserialize**（`types.py:215-274`）：所有 dataclass 递归转 dict，datetime 转 ISO 8601 字符串，缺失字段回退默认值保证向后兼容。这是会话持久化的基础。

---

## 四、会话保持逻辑

会话保持分两层：**REPL 跨轮历史**（内存）和 **SessionManager 持久化**（磁盘）。

### 4.1 REPL 跨轮对话历史

文件：[repl.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/repl.py)

#### `_conversation_history` 字段（`repl.py:85-87`）

REPL 层维护的跨轮对话历史，是 `list[dict[str, Any]]`，**不依赖 AgentState**。每次 `Agent.run()` 创建新的 AgentState，跨轮对话通过 `_conversation_history` 在 REPL 层维护。

#### 执行任务时的注入与回写（`repl.py:265-389`，`_execute_task`）

1. 渲染用户消息 + 分隔线 + AI 标签（`repl.py:281-285`）
2. 订阅 5 个事件（`repl.py:346-350`）：`AFTER_LLM_CALL` / `BEFORE_TOOL_CALL` / `AFTER_TOOL_CALL` / `ON_ERROR` / `ON_FINISH`
3. **调用 `agent.run(user_input, initial_messages=self._conversation_history or None)`**（`repl.py:354-359`）—— 把历史作为 `initial_messages` 传入
4. **执行后回写新轮次到 `_conversation_history`**（`repl.py:361-364`）：

```python
self._conversation_history.append({"role": "user", "content": user_input})
for msg in state.messages:
    if msg.get("role") == "assistant":
        self._conversation_history.append(msg)
```

> **注意**：这里**只追加 user + assistant 消息**，**不追加 tool 消息和 assistant-with-tool_calls 中间步骤**。跨轮历史是"清洁"的对话流，不包含中间的工具调用细节（下一轮 LLM 不会看到上一轮的工具结果配对）。

5. `finally` 块取消所有事件订阅（`repl.py:384-389`），避免跨任务事件泄露

#### 会话恢复（`repl.py:213-259`，`restore_session`）

- 接受 `AgentState` 或 dict
- `AgentState` → `self._conversation_history = list(session.messages)`
- dict → 从 `session["state"]["messages"]` 提取
- 用于 `nexus --continue`（[main.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/main.py) 的 `_run_continue`：`mgr.load_latest()` → `repl.restore_session(session)` → `repl.run()`）

#### 内置命令

- `/clear`：重新创建 Agent（保留 LLM/Policy/配置/工具），`_conversation_history.clear()`（`repl.py:420-438`）
- `/save`：构造 `AgentState(task="repl session", messages=list(self._conversation_history))` 调 `session_mgr.save()`（`repl.py:441-455`）
- `/tools`：列出 `agent.tool_registry` 中工具
- `/quit` `/exit` `/help`

### 4.2 SessionManager 持久化

文件：[session.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/session.py)

#### 存储位置

默认 `~/.nexus/sessions/`（`session.py:65-69`），每个会话一个 JSON 文件，以 `session_id` 命名。

#### save()（`session.py:82-140`）

1. 生成 8 位 uuid 前缀作为 `session_id`（`session.py:108`）
2. 生成摘要：首条 user 消息前 80 字符，无则用 task 兜底（`session.py:111, 280-302`）
3. **auto_truncate=True 时先截断再序列化**（`session.py:114-118`）：
   - `_truncate_state`（`session.py:304-359`）保留所有 system 消息 + 最近 `50 轮`（每轮 user+assistant 共 2 条，共 100 条非 system 消息）
   - 返回新 `AgentState` 副本，不污染原 state
4. 写入 JSON 文件，结构：

```json
{
  "session_id": "...",
  "version": "1.0",
  "created_at": "<ISO>",
  "summary": "...",
  "metadata": {...},
  "state": "<serialized AgentState>"
}
```

#### load()（`session.py:142-177`）

- 按 `session_id` 找文件，不存在返回 None
- `AgentState.deserialize(data["state"])` 恢复

#### load_latest()（`session.py:179-197`）

- 调 `list_sessions()` 取第一条（按 mtime 倒序），再 `load()`
- 用于 `nexus --continue`

#### list_sessions()（`session.py:199-245`）

- `glob("*.json")` 按 mtime 降序
- 损坏 JSON 静默跳过
- 最多返回 20 条，每条含 `id` / `timestamp` / `summary` / `message_count`

#### delete()（`session.py:247-274`）

- `filepath.unlink()`，不存在返回 False

### 4.3 会话保持的两层关系

```
┌─────────────────────────────────────────────────┐
│  REPL 进程（内存）                                │
│  ┌───────────────────────────────────────────┐   │
│  │  _conversation_history（跨轮清洁对话流）    │   │
│  │  [user, assistant, user, assistant, ...]   │   │
│  └───────────────┬───────────────────────────┘   │
│                  │ /save 或退出时                  │
│                  ▼                                │
│  ┌───────────────────────────────────────────┐   │
│  │  SessionManager.save(state)                │   │
│  │  → ~/.nexus/sessions/<id>.json             │   │
│  └───────────────┬───────────────────────────┘   │
└──────────────────┼────────────────────────────────┘
                   │ nexus --continue
                   ▼
┌─────────────────────────────────────────────────┐
│  新 REPL 进程                                    │
│  SessionManager.load_latest()                   │
│  → restore_session(state)                       │
│  → _conversation_history = state.messages       │
│  → 继续对话                                      │
└─────────────────────────────────────────────────┘
```

---

## 五、工具调用逻辑

### 5.1 工具定义：BaseTool

文件：[base.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/base.py)（`BaseTool` 在 `base.py:108-251`）

- 抽象属性：`name` / `description` / `schema`（JSON Schema）
- 抽象方法：`async execute(args) -> ToolResult`
- 可选 `setup()` / `teardown()`（默认空实现，`base.py:237-251`）
- `to_openai_schema()`（`base.py:222-235`）生成 `{"type":"function","function":{name,description,parameters}}`

### 5.2 工具注册：ToolRegistry

文件：[registry.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/registry.py)

- `name → BaseTool` 字典，O(1) 查找（`registry.py:54-55`）
- 重复注册抛 `ValueError`（`registry.py:74-80`），强制 `unregister` 后再注册
- `to_openai_schemas()`（`registry.py:128-139`）批量导出所有工具为 OpenAI function calling 格式，供 Policy 构造 `LLMCallAction.tools`

### 5.3 工具执行：ToolExecutor

文件：[executor.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/executor.py)（`execute` 在 `executor.py:183-306`）

- `execute()` 永远返回 `ToolResult`，不抛异常（`executor.py:204-206`）
- 流程：
  1. 查工具（不存在 → `ToolResult.fail(ToolNotFoundError)`）
  2. 校验参数（`_validate_args`，基于 JSON Schema 的 required + type 检查，`executor.py:103-181`）
  3. `setup()`（失败仅 warning 不阻塞）
  4. `tool.execute(args)` + 计时
  5. 包装 `ToolResult`（成功 / 失败）
  6. `teardown()`（失败仅 warning 不阻塞）
- 所有异常（`ToolNotFoundError` / `ToolValidationError` / 工具自身异常）都被捕获转为 `ToolResult.fail(...)`

### 5.4 工具调用在 Runtime 中的执行

文件：[runtime.py:386-492](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py)（`_execute_tool_call`）

1. 派发 `BEFORE_TOOL_CALL`（`runtime.py:410-419`）
2. 调用 `self._tool_executor.execute(tool_name, tool_call_id, arguments)`（`runtime.py:432-436`）
3. 创建 `ToolCallRecord` 并 `state.add_tool_call(record)`（`runtime.py:439-446`）
4. **将 tool 结果作为 `role="tool"` 消息追加到 `state.messages`**（`runtime.py:449-453`）：

```python
state.messages.append({
    "role": "tool",
    "tool_call_id": action.tool_call_id,
    "content": str(result.data) if result.data is not None else result.error or "",
})
```

5. 更新 `state.variables["_last_tool_result"]`（`runtime.py:456`）
6. 创建 `Step(step_type="tool_call", tool_calls=[record])` 并 `state.add_step(step)`（`runtime.py:459-463`）
7. 派发 `AFTER_TOOL_CALL`（`runtime.py:466-476`）

### 5.5 LLM 调用与 Response 处理

文件：[runtime.py:256-384](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py)（`_execute_llm_call`）

1. 派发 `BEFORE_LLM_CALL`（`runtime.py:285-295`），payload 含 model/provider/messages/tools
2. 调用 `context.llm.chat(messages=action.messages, tools=action.tools)`（`runtime.py:308-311`），返回 `LLMResponse`（定义在 [base.py:76-107](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/llm/base.py)，含 `content`/`tool_calls`/`usage`/`model`/`finish_reason`）
3. **构造 assistant 消息并追加到 `state.messages`**（`runtime.py:313-338`，见 3.2）
4. **存储完整响应到 `state.variables["_last_llm_response"]`**（`runtime.py:341-347`），供 Policy 读取结构化 tool_calls
5. 创建 `Step(step_type="llm_call", input_messages=<快照>, output_content=response.content)` 并 `state.add_step(step)`（`runtime.py:350-355`）
6. 派发 `AFTER_LLM_CALL`（`runtime.py:358-372`），payload 含 response/usage

### 5.6 工具调用完整流程图

```
LLM 返回 response（含 tool_calls）
   │
   ▼
[ReActPolicy._handle_after_llm]
   - 解析 arguments（JSON 字符串 → dict）
   - 缓存到 _pending_tool_calls 队列
   - 弹出第一个 → ToolCallAction(name, id, args)
   │
   ▼
[Runtime._execute_tool_call]
   - BEFORE_TOOL_CALL 事件
   - tool_executor.execute(name, id, args)
       │
       ▼
   [ToolExecutor]
       - registry.get(name) → BaseTool
       - _validate_args(args, schema)
       - tool.setup()
       - tool.execute(args) → ToolResult
       - tool.teardown()
       - 返回 ToolResult（永不抛异常）
       │
       ▼
   - 创建 ToolCallRecord → state.add_tool_call
   - 追加 {role:tool, tool_call_id, content} → state.messages
   - AFTER_TOOL_CALL 事件
   │
   ▼
[ReActPolicy._handle_after_tool]
   - 队列非空？→ 弹出下一个 ToolCallAction（同批次继续）
   - 队列空 + 未超限？→ LLMCallAction（把工具结果回馈给 LLM）
   - 队列空 + 超限？→ ErrorAction("Max steps exceeded")
```

---

## 六、事件系统

文件：[event_types.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/event/event_types.py) + [event_bus.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/event/event_bus.py)

### 6.1 8 类事件（`event_types.py:19-52`）

`BEFORE_AGENT_RUN` / `AFTER_AGENT_RUN` / `BEFORE_LLM_CALL` / `AFTER_LLM_CALL` / `BEFORE_TOOL_CALL` / `AFTER_TOOL_CALL` / `ON_ERROR` / `ON_FINISH`

典型一次运行顺序：

```
BEFORE_AGENT_RUN
  → [BEFORE_LLM_CALL → AFTER_LLM_CALL
     → BEFORE_TOOL_CALL → AFTER_TOOL_CALL]*   （ReAct 循环）
  → AFTER_AGENT_RUN
  → ON_FINISH
```

### 6.2 EventBus 特性

- async pub/sub，`defaultdict(EventType → list[handler])`
- `publish` 用 `asyncio.TaskGroup` 并发调度所有 handler（`event_bus.py:87-89`）
- **异常隔离**：单个 handler 抛异常不中断其他 handler，仅 warning 日志（`event_bus.py:149-160`）
- `subscribe` 幂等（`event_bus.py:111-113`），`unsubscribe` 静默（`event_bus.py:127-133`）

---

## 七、关键设计观察

### 7.1 架构优点

1. **策略与执行解耦**：Policy 是纯决策函数，Runtime 负责执行副作用，便于替换策略（如未来引入 Plan-Execute、Reflect 等模式）
2. **State 作为唯一真实来源**：所有中间状态都沉淀在 AgentState，便于序列化、恢复、调试
3. **工具执行容错**：ToolExecutor 永不抛异常，所有错误转为 ToolResult.fail，保证 Agent Loop 不因工具异常崩溃
4. **事件驱动的展示层**：REPL 通过订阅 EventBus 实现流式展示，与业务逻辑完全解耦

### 7.2 潜在改进点

1. **`_conversation_history` 只存 user/assistant，丢失 tool 中间步骤**（[repl.py:361-364](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/repl.py)）：下一轮 LLM 看不到上一轮的工具调用配对。若上一轮 LLM 用了工具，跨轮历史里只有最终 assistant 文本回复，没有 `tool_calls` 和 `tool` 消息。在多轮工具密集场景下可能丢失上下文。

2. **`Step.input_messages` 是完整 messages 快照**（[runtime.py:352](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py)）：每次 LLM 调用都 `list(action.messages)` 存一份，长对话下 steps 体积会平方级膨胀。

3. **`state.variables["_last_llm_response"]` 是隐式契约**（`runtime.py:341-347`）：ReActPolicy 实际从 `state.messages[-1]` 读 tool_calls（`react_policy.py:135-136`），并未用到 variables 里的缓存。该字段目前更像是冗余备份。

4. **`_step_count` 与 `current_step` 双计数器**：Policy 内部 `_step_count` 仅在进入 LLM 阶段递增（工具调用不计入），Runtime 的 `current_step` 每个 Action 都递增。两者语义不同但容易混淆。

5. **`SessionManager.save` 每次生成新 session_id**（[session.py:108](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/session.py)）：同一 REPL 会话多次 `/save` 会产生多个文件，而非覆盖更新。`load_latest` 只能取到最近一次快照。

---

## 附录：核心文件索引

| 模块 | 文件 |
|------|------|
| Agent 门面 | [nexus/core/agent/agent.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/agent/agent.py) |
| Runtime 调度 | [nexus/core/runtime/runtime.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/runtime/runtime.py) |
| ReAct 策略 | [nexus/core/executor/react_policy.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/executor/react_policy.py) |
| 策略基类 | [nexus/core/executor/policy.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/executor/policy.py) |
| Action 类型 | [nexus/core/executor/actions.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/executor/actions.py) |
| AgentState | [nexus/core/state/types.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/state/types.py) |
| 执行上下文 | [nexus/core/context/context.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/context/context.py) |
| 事件类型 | [nexus/core/event/event_types.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/event/event_types.py) |
| EventBus | [nexus/core/event/event_bus.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/core/event/event_bus.py) |
| LLM 基类 | [nexus/llm/base.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/llm/base.py) |
| 工具基类 | [nexus/tools/base.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/base.py) |
| 工具注册 | [nexus/tools/registry.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/registry.py) |
| 工具执行器 | [nexus/tools/executor.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/tools/executor.py) |
| 会话管理 | [nexus/cli/session.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/session.py) |
| REPL | [nexus/cli/repl.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/repl.py) |
| CLI 入口 | [nexus/cli/main.py](file:///Users/wanghanle/Documents/code/githubProject/Nexus/nexus/cli/main.py) |
