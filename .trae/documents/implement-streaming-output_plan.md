# 流式输出（思考链 + AI 回复）实施计划

## Summary

打通 CLI 流式输出调用链：Runtime 改用 `stream_chat()` + 派发 `LLM_CHUNK` 事件，Provider 层补充 OpenAI `reasoning_content` 和 Anthropic `thinking` block 解析，CLI 层订阅 chunk 事件用 Rich Live 增量渲染思考链（灰色）和回复（正常色）。通过 `config.stream`（默认 `True`）控制开关，可回退非流式。

## Current State Analysis

### 已就绪部分
- `BaseLLM.stream_chat()` 抽象方法已定义（`nexus/llm/base.py:209-233`）
- OpenAI Provider 的 `stream_chat()` 已实现（`openai.py:238-371`），`stream=True` + tool_calls 增量聚合
- Anthropic Provider 的 `stream_chat()` 已实现（`anthropic.py:230-331`），处理 `text_delta` / `input_json_delta` / `content_block_*` 事件
- `display.py:109-148` 的 `render_streaming_response()` 已实现 Rich Live + Markdown 增量渲染，**但从未被调用**

### 断层点（需修复）
1. **Runtime**：`_execute_llm_call`（`runtime.py:308-311`）调用 `llm.chat()` 非流式，未调用 `stream_chat()`
2. **事件系统**：`EventType`（`event_types.py:19-52`）无 `LLM_CHUNK` 事件
3. **LLMChunk**（`base.py:108-127`）：无 `delta_reasoning` 字段，无法承载思考链增量
4. **Provider 解析缺失**：
   - OpenAI `stream_chat`（`openai.py:319`）只读 `delta.content`，未读 `delta.reasoning_content`（o1/o3 系列推理字段）
   - Anthropic `stream_chat`（`anthropic.py:276-314`）只识别 `text_delta` / `input_json_delta`，未识别 `thinking_delta`（Claude extended thinking）
5. **CLI**：`repl.py:291-304` 和 `main.py:179-189` 的 `on_after_llm` 一次性渲染，未订阅 chunk 事件
6. **配置**：`NexusConfig`（`config.py:94-112`）无 `stream` 字段

## Proposed Changes

### 1. 数据结构层 — `nexus/llm/base.py`

**LLMChunk 增加 `delta_reasoning` 字段**（`base.py:108-127`）

```python
@dataclass
class LLMChunk:
    delta_content: str = ""
    delta_reasoning: str = ""    # 新增：思考链增量（OpenAI reasoning_content / Anthropic thinking）
    delta_tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
```

更新 docstring 说明 `delta_reasoning` 的来源。

### 2. 事件系统 — `nexus/core/event/event_types.py`

**新增 `LLM_CHUNK` 事件类型**（`event_types.py:48` 附近）

```python
LLM_CHUNK = auto()
"""流式 LLM 调用过程中每个 chunk 到达时触发。
payload 含 delta_content、delta_reasoning、delta_tool_calls、finish_reason。"""
```

### 3. OpenAI Provider — `nexus/llm/providers/openai.py`

**`stream_chat` 解析 `reasoning_content`**（`openai.py:319` 附近）

在 `delta_content: str = delta.content or ""` 之后增加：
```python
delta_reasoning: str = ""
# OpenAI o1/o3 系列的推理内容（reasoning_content 字段）
if hasattr(delta, "reasoning_content") and delta.reasoning_content:
    delta_reasoning = delta.reasoning_content
```

在 yield LLMChunk 时（`openai.py:367-370`）传入 `delta_reasoning=delta_reasoning`。

### 4. Anthropic Provider — `nexus/llm/providers/anthropic.py`

**`stream_chat` 解析 thinking block**（`anthropic.py:276-314`）

在 `content_block_start` 事件处理中识别 `block.type == "thinking"`（`anthropic.py:278` 附近）。
在 `content_block_delta` 中识别 `delta.type == "thinking_delta"`，提取 `delta.thinking` 文本：

```python
elif event.type == "content_block_start":
    block = event.content_block
    if block.type == "tool_use":
        pending_tool_calls[event.index] = {...}
    # thinking block 无需特殊初始化，其增量在 thinking_delta 中处理

elif event.type == "content_block_delta":
    delta = event.delta
    if delta.type == "text_delta":
        delta_content = delta.text
    elif delta.type == "thinking_delta":
        delta_reasoning = delta.thinking  # 新增
    elif delta.type == "input_json_delta":
        ...
```

在 yield LLMChunk 时传入 `delta_reasoning=delta_reasoning`，并在 yield 条件（`anthropic.py:317`）加入 `delta_reasoning`。

### 5. Runtime 层 — `nexus/core/runtime/runtime.py`

**`_execute_llm_call` 改造为流式路径**（`runtime.py:256-384`）

在派发 `BEFORE_LLM_CALL` 后（`runtime.py:295`），根据 `context.variables.get("_stream", True)` 分流：

```python
# 2. 调用 LLM（流式或非流式）
stream_mode = context.variables.get("_stream", True)

if stream_mode and hasattr(context.llm, "stream_chat"):
    # 流式路径：聚合 chunks + 派发 LLM_CHUNK 事件
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None
    usage = None

    async for chunk in await context.llm.stream_chat(
        messages=action.messages, tools=action.tools,
    ):
        if chunk.delta_content:
            content_parts.append(chunk.delta_content)
        if chunk.delta_reasoning:
            reasoning_parts.append(chunk.delta_reasoning)
        if chunk.delta_tool_calls:
            tool_calls = chunk.delta_tool_calls  # 取最后累积状态
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason

        # 派发 LLM_CHUNK 事件
        await self._event_bus.publish(Event(
            type=EventType.LLM_CHUNK,
            payload={
                "delta_content": chunk.delta_content,
                "delta_reasoning": chunk.delta_reasoning,
                "delta_tool_calls": chunk.delta_tool_calls,
                "finish_reason": chunk.finish_reason,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

    # 构造 LLMResponse 供后续逻辑复用
    response = LLMResponse(
        content="".join(content_parts),
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        model=getattr(context.llm, "model", "unknown"),
    )
else:
    # 非流式回退路径（原逻辑）
    response = await context.llm.chat(messages=action.messages, tools=action.tools)
```

后续的 assistant_msg 写入、variables 存储、Step 创建、AFTER_LLM_CALL 派发逻辑保持不变（`runtime.py:313-384`），都基于聚合后的 `response`。

### 6. 配置层 — `nexus/cli/config.py`

**NexusConfig 增加 `stream` 字段**（`config.py:94-112`）

```python
@dataclass
class NexusConfig:
    ...
    stream: bool = True  # 流式输出开关，默认开启
```

更新顶部 docstring 的配置文件示例，添加 `stream: true` 注释。

### 7. Agent 层 — `nexus/core/agent/agent.py`

**`run()` 将 stream 配置注入 context.variables**

在 `agent.py:238-245` 构造 ExecutionContext 时（或 runtime 构造 context 时），设置：
```python
context.variables["_stream"] = self.stream_enabled  # 或从 config 传入
```

具体注入点：Agent 构造时接受 `stream: bool = True` 参数，或 Runtime 构造时接受。**决策：Agent 构造函数增加 `stream: bool = True` 参数**，`run()` 时写入 `context.variables["_stream"]`。

CLI 创建 Agent 时传入 `stream=config.stream`（`main.py` 的 `_create_agent` 或 REPL 初始化处）。

### 8. CLI 展示层 — `nexus/cli/display.py`

**新增增量渲染方法**

```python
def start_streaming_thinking(self) -> Live:
    """开启思考链流式渲染，返回 Live 实例供后续更新。"""
    # 灰色 dim italic Panel，🤔 Thinking 标签

def update_streaming_thinking(self, live: Live, accumulated: str) -> None:
    """更新思考链累积内容。"""

def start_streaming_response(self) -> Live:
    """开启回复流式渲染，返回 Live 实例。"""

def update_streaming_response(self, live: Live, accumulated: str) -> None:
    """更新回复累积内容。"""
```

利用已有的 `render_streaming_response` 逻辑（`display.py:109-148`），但拆分为 start/update/stop 三阶段以适配事件驱动。

**决策：思考链和回复用两个独立的 Rich Live 实例，先后渲染。**先渲染 thinking（灰色），thinking 流结束后切换到 response（正常色 Markdown）。

### 9. CLI REPL — `nexus/cli/repl.py`

**`_execute_task` 订阅 `LLM_CHUNK` 事件**（`repl.py:291-304` 附近）

```python
# 流式状态（每轮 LLM 调用重置）
stream_state = {"thinking_live": None, "response_live": None,
                "thinking_acc": "", "response_acc": ""}

async def on_llm_chunk(event: Event) -> None:
    delta_content = event.payload.get("delta_content", "")
    delta_reasoning = event.payload.get("delta_reasoning", "")
    
    if delta_reasoning:
        if stream_state["thinking_live"] is None:
            stream_state["thinking_live"] = self.display.start_streaming_thinking()
        stream_state["thinking_acc"] += delta_reasoning
        self.display.update_streaming_thinking(
            stream_state["thinking_live"], stream_state["thinking_acc"])
    
    if delta_content:
        # thinking 结束后切换到 response
        if stream_state["thinking_live"] is not None:
            stream_state["thinking_live"].stop()
            stream_state["thinking_live"] = None
        if stream_state["response_live"] is None:
            stream_state["response_live"] = self.display.start_streaming_response()
        stream_state["response_acc"] += delta_content
        self.display.update_streaming_response(
            stream_state["response_live"], stream_state["response_acc"])
```

在 `on_after_llm` 中（`repl.py:291-304`）：
- 关闭可能未关闭的 Live 实例
- 如果流式已渲染了 response，不再重复调用 `render_response`
- 如果流式渲染了 thinking（有 tool_calls），不再重复调用 `render_thinking`
- 仍处理 usage 统计

**订阅注册**：在 `repl.py:346-350` 的订阅列表中加入 `EventType.LLM_CHUNK`。

移除 `with self.display.show_spinner(...)` 包裹（流式渲染时 spinner 会干扰 Live），或改为仅在非流式时使用 spinner。

### 10. CLI main — `nexus/cli/main.py`

**`_run_single` 同步改造**（`main.py:157-224`）

与 repl.py 相同的 on_llm_chunk 订阅逻辑。Agent 创建时传入 `stream=config.stream`。

### 11. 测试

新增 `tests/test_streaming.py`：
- 测试 `LLMChunk.delta_reasoning` 字段存在
- 测试 OpenAI provider stream_chat 解析 reasoning_content（mock SDK chunk）
- 测试 Anthropic provider stream_chat 解析 thinking_delta（mock SDK event）
- 测试 Runtime 流式路径派发 LLM_CHUNK 事件（mock provider 返回 chunks）
- 测试 `config.stream` 默认 True、可设为 False

更新现有测试：
- `test_openai_provider.py` / `test_anthropic_provider.py` 的 stream_chat 测试补充 reasoning 断言

## Assumptions & Decisions

1. **被动解析 thinking**：Provider 层只做"如果响应里有就解析"，不主动在 API 请求中开启 thinking 功能。OpenAI o1/o3 的 `reasoning_content` 是模型自带返回的；Anthropic 的 thinking block 需要用户在 provider 配置里通过 `kwargs` 透传 `thinking={"type": "enabled", ...}` 开启，本计划不自动注入。

2. **`LLMChunk.delta_reasoning` 为增量**：与 `delta_content` 一致，每次 chunk 携带增量文本，下游拼接。

3. **流式时不用 spinner**：Rich Live 渲染与 spinner 冲突，流式模式下移除 spinner 包裹，非流式模式保留 spinner。

4. **tool_calls 在流式中取最后累积状态**：Runtime 聚合时 `tool_calls = chunk.delta_tool_calls`（最后一个非空 chunk 即完整状态），与现有 `stream_chat` 的累积语义一致（`anthropic.py:240-246` docstring 说明）。

5. **回退路径保留**：`config.stream=False` 或 provider 无 `stream_chat` 时回退到 `chat()` 非流式，保持现有行为。

6. **usage 在流式下可能为 None**：部分 provider 流式不返回 usage，`AFTER_LLM_CALL` 的 usage 字段允许 None（现有代码已处理 `response.usage` 为 None 的情况）。

## Verification Steps

1. 运行 `pytest tests/test_streaming.py -v` — 新增流式测试全部通过
2. 运行 `pytest tests/ -v` — 全部测试通过，无回归
3. 手动验证：启动 REPL，执行任务，观察：
   - 思考链（如有）灰色增量显示
   - AI 回复逐字显示
   - 工具调用时思考链 Panel 关闭，切换到工具调用 Panel
4. 手动验证 `nexus.yaml` 设 `stream: false` 后回退到非流式（一次性渲染 + spinner）
5. 验证 OpenAI o1/o3 模型（如有 key）返回 reasoning_content 时正确流式展示
6. 验证 Anthropic Claude thinking 开启时正确流式展示
