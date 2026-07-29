"""测试 ReActPolicy 的决策逻辑。

覆盖范围
--------
- 首次调用（无历史步骤）返回 LLMCallAction
- LLM 返回 tool_calls 时返回 ToolCallAction
- LLM 返回无 tool_calls 时返回 FinishAction
- 工具执行后回到 LLM（返回 LLMCallAction）
- 超过 max_steps 时返回 ErrorAction
- 同类多个 tool_calls 按批次逐一派发
"""

import pytest
from nexus.core.executor.react_policy import ReActPolicy
from nexus.core.executor.actions import (
    LLMCallAction,
    ToolCallAction,
    FinishAction,
    ErrorAction,
)
from nexus.core.context.context import ExecutionContext
from nexus.core.state.types import AgentState, Step, ToolCallRecord
from nexus.core.event.event_bus import EventBus
from nexus.tools.registry import ToolRegistry
from nexus.tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# 辅助：构造测试用的 ExecutionContext
# ---------------------------------------------------------------------------


def _make_context(state=None, max_steps=20):
    """构造测试用的 ExecutionContext。

    创建一个最小化的运行时上下文，包含 ToolRegistry（含 2 个 mock 工具）、
    ToolExecutor、EventBus 和给定的 AgentState（或空 state）。
    """
    if state is None:
        state = AgentState(task="test task")
    registry = ToolRegistry()

    # 注册 mock 工具以支持 to_openai_schemas()
    from nexus.tools.base import BaseTool, ToolResult

    class _MockTool(BaseTool):
        name = "mock_search"
        description = "mock"
        schema = {"type": "object", "properties": {}, "required": []}

        async def execute(self, args):
            return ToolResult(success=True, data=args, tool_name=self.name)

    class _MockTool2(BaseTool):
        name = "mock_calc"
        description = "mock calculator"
        schema = {"type": "object", "properties": {}, "required": []}

        async def execute(self, args):
            return ToolResult(success=True, data=args, tool_name=self.name)

    registry.register(_MockTool())
    registry.register(_MockTool2())

    executor = ToolExecutor(registry)
    events = EventBus()

    return ExecutionContext(
        state=state,
        llm=None,  # Policy 不直接调用 LLM
        tool_executor=executor,
        events=events,
        max_steps=max_steps,
        variables=state.variables,
    )


def _add_llm_step(state, messages=None, tool_calls_data=None):
    """向 state 添加一个 llm_call 类型的 step 和对应的 assistant 消息。

    模拟 Runtime 在 _execute_llm_call 中完成的写入操作。

    Parameters
    ----------
    state : AgentState
        要修改的状态。
    messages : list[dict] | None
        要写入 state.messages 的 assistant 消息。若为 None，自动构造。
    tool_calls_data : list[dict] | None
        OpenAI 格式的 tool_calls 数据，用于构造 assistant 消息。
        若为 None，表示无 tool_calls。
    """
    if messages is None:
        if tool_calls_data:
            messages = [
                {"role": "assistant", "content": None, "tool_calls": tool_calls_data}
            ]
        else:
            messages = [{"role": "assistant", "content": "The answer is 42."}]

    for msg in messages:
        state.add_message(msg["role"], msg.get("content", ""))
        # 如果消息包含额外的字段（如 tool_calls），需要手动追加
        if len(state.messages) == 1 or state.messages[-1].get("role") == "assistant":
            # 把额外字段合并到最后的 message 中
            last_msg = state.messages[-1]
            for key, value in msg.items():
                if key not in ("role", "content"):
                    last_msg[key] = value

    step = Step(
        step_type="llm_call",
        input_messages=[],
        output_content=messages[-1].get("content", "") if messages else "",
    )
    state.add_step(step)


def _add_tool_step(state, tool_name="mock_search", tool_call_id="call_001", arguments=None):
    """向 state 添加一个 tool_call 类型的 step 和对应的 tool 消息。

    模拟 Runtime 在 _execute_tool_call 中完成的写入操作。
    """
    if arguments is None:
        arguments = {"query": "test"}
    state.add_message("tool", f"Result for {tool_name}")

    # 修正最后一条 tool 消息，添加 tool_call_id
    state.messages[-1]["tool_call_id"] = tool_call_id

    record = ToolCallRecord(
        tool_name=tool_name,
        arguments=arguments,
        result={"data": "mock result"},
    )
    state.add_tool_call(record)

    step = Step(
        step_type="tool_call",
        tool_calls=[record],
    )
    state.add_step(step)


# ---------------------------------------------------------------------------
# ReActPolicy 测试
# ---------------------------------------------------------------------------


class TestReActPolicy:
    """测试 ReActPolicy 的决策逻辑（状态机）。"""

    @pytest.mark.asyncio
    async def test_first_action_is_llm_call(self):
        """首次调用（无历史步骤）应返回 LLMCallAction。"""
        state = AgentState(task="test task")
        state.add_message("user", "What is 2+2?")
        context = _make_context(state)

        policy = ReActPolicy(max_steps=20)
        action = await policy.next_action(context)

        assert isinstance(action, LLMCallAction)
        # 应包含用户消息
        assert len(action.messages) == 1
        assert action.messages[0]["role"] == "user"
        # 应包含工具 schemas（2 个 mock 工具）
        assert action.tools is not None
        assert len(action.tools) == 2

    @pytest.mark.asyncio
    async def test_llm_response_with_tool_calls(self):
        """LLM 返回 tool_calls 时应返回 ToolCallAction。"""
        state = AgentState(task="test task")
        state.add_message("user", "Search for Python tutorials")

        # 添加一个 llm_call step，消息中带 tool_calls
        tool_calls_data = [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "mock_search",
                    "arguments": '{"query": "Python tutorials"}',
                },
            }
        ]
        _add_llm_step(state, tool_calls_data=tool_calls_data)

        context = _make_context(state)
        policy = ReActPolicy(max_steps=20)
        # 首次调用触发 LLM step 计数，但步骤已被手动添加
        # 我们需要手动设置 _step_count 以匹配当前状态
        policy._step_count = 1
        action = await policy.next_action(context)

        assert isinstance(action, ToolCallAction)
        assert action.tool_name == "mock_search"
        assert action.tool_call_id == "call_abc123"
        assert action.arguments == {"query": "Python tutorials"}

    @pytest.mark.asyncio
    async def test_llm_response_without_tool_calls(self):
        """LLM 返回无 tool_calls 的响应时应返回 FinishAction。"""
        state = AgentState(task="test task")
        state.add_message("user", "What is 2+2?")

        # 添加一个 llm_call step，消息无 tool_calls
        _add_llm_step(state)  # 默认生成 "The answer is 42."

        context = _make_context(state)
        policy = ReActPolicy(max_steps=20)
        policy._step_count = 1
        action = await policy.next_action(context)

        assert isinstance(action, FinishAction)
        assert action.result == "The answer is 42."

    @pytest.mark.asyncio
    async def test_after_tool_execution(self):
        """工具执行后应回到 LLM 继续推理（返回 LLMCallAction）。"""
        state = AgentState(task="test task")
        state.add_message("user", "Search for Python")

        # 模拟一轮完整的 ReAct 循环：
        # 1. LLM 返回 tool_calls → ToolCallAction
        # 2. 工具执行完毕 → 下一个 action
        tool_calls_data = [
            {
                "id": "call_xyz",
                "type": "function",
                "function": {
                    "name": "mock_search",
                    "arguments": '{"query": "Python"}',
                },
            }
        ]
        _add_llm_step(state, tool_calls_data=tool_calls_data)
        _add_tool_step(state, tool_name="mock_search", tool_call_id="call_xyz")

        context = _make_context(state)
        policy = ReActPolicy(max_steps=20)
        # 模拟：第一步 LLM 调用了 tool，_step_count=1，_pending_tool_calls 已被消费
        policy._step_count = 1
        policy._pending_tool_calls = []  # 队列已空

        action = await policy.next_action(context)

        assert isinstance(action, LLMCallAction)
        # 此时 _step_count 应递增为 2
        assert policy._step_count == 2
        # 应包含当前的对话历史（user + assistant + tool）
        assert len(action.messages) >= 3

    @pytest.mark.asyncio
    async def test_max_steps_exceeded(self):
        """超过 max_steps 时应返回 ErrorAction。"""
        state = AgentState(task="test task")
        state.add_message("user", "Search")

        # 模拟一轮循环后工具执行完毕
        tool_calls_data = [
            {
                "id": "call_001",
                "type": "function",
                "function": {
                    "name": "mock_search",
                    "arguments": '{"query": "test"}',
                },
            }
        ]
        _add_llm_step(state, tool_calls_data=tool_calls_data)
        _add_tool_step(state, tool_name="mock_search", tool_call_id="call_001")

        context = _make_context(state)
        policy = ReActPolicy(max_steps=1)
        # 模拟：第一步已经执行过 LLM 调用，_step_count=1 达到上限
        policy._step_count = 1
        policy._pending_tool_calls = []

        action = await policy.next_action(context)

        assert isinstance(action, ErrorAction)
        assert "Max steps exceeded" in action.error

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_batch(self):
        """同一批次多个 tool_calls 应逐一返回 ToolCallAction。"""
        state = AgentState(task="test task")
        state.add_message("user", "Find and calculate")

        # LLM 返回两个 tool_calls
        tool_calls_data = [
            {
                "id": "call_a",
                "type": "function",
                "function": {
                    "name": "mock_search",
                    "arguments": '{"query": "weather"}',
                },
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {
                    "name": "mock_calc",
                    "arguments": '{"expression": "1+1"}',
                },
            },
        ]
        _add_llm_step(state, tool_calls_data=tool_calls_data)

        context = _make_context(state)
        policy = ReActPolicy(max_steps=20)
        policy._step_count = 1

        # 第一个 tool_call：应从队列中弹出
        action1 = await policy.next_action(context)
        assert isinstance(action1, ToolCallAction)
        assert action1.tool_name == "mock_search"
        assert action1.tool_call_id == "call_a"

        # 此时队列中应还有一个
        assert len(policy._pending_tool_calls) == 1

        # 模拟执行完第一个工具
        _add_tool_step(state, tool_name="mock_search", tool_call_id="call_a")

        # 第二个 tool_call：队列非空，继续派发
        action2 = await policy.next_action(context)
        assert isinstance(action2, ToolCallAction)
        assert action2.tool_name == "mock_calc"
        assert action2.tool_call_id == "call_b"

        # 队列应已空
        assert len(policy._pending_tool_calls) == 0

    @pytest.mark.asyncio
    async def test_unknown_step_type_fallback(self):
        """未知 step_type 应安全回退到 FinishAction。"""
        state = AgentState(task="test task")
        state.add_message("user", "hello")

        # 手动添加一个未知类型的 step
        step = Step(step_type="unknown_type", output_content="???")
        state.add_step(step)

        context = _make_context(state)
        policy = ReActPolicy(max_steps=20)
        action = await policy.next_action(context)

        assert isinstance(action, FinishAction)
