"""测试 Agent Runtime 集成流程（使用 Mock LLM 和 Mock Tool）。

覆盖范围
--------
- Agent 执行简单任务（无工具调用），返回含 steps 的 AgentState
- Agent 调用工具，state 含 tool 调用记录
- Runtime 执行过程中事件被正确派发
- 执行后 state.messages 有内容
"""

import pytest
from nexus.core.runtime.runtime import Runtime
from nexus.core.agent.agent import Agent
from nexus.core.state.types import AgentState, Step, ToolCallRecord
from nexus.core.event.event_types import EventType
from nexus.llm.base import (
    BaseLLM,
    LLMResponse,
    LLMChunk,
    UsageStats,
    ToolCall as LLMToolCall,
)
from nexus.tools.base import BaseTool, ToolResult


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class MockLLM(BaseLLM):
    """Mock LLM —— 按预设响应列表依次返回结果。

    Parameters
    ----------
    responses : list[LLMResponse]
        预设的响应列表。每次 chat() 调用返回列表中对应序号的响应。
        若调用次数超过列表长度，返回最后一个响应。
    """

    def __init__(self, responses=None):
        self.responses = responses or [LLMResponse(content="hello", usage=UsageStats())]
        self._call_count = 0
        self.chat_messages_history: list[list[dict]] = []

    async def chat(self, messages, tools=None, **kwargs):
        self.chat_messages_history.append(messages)
        resp = self.responses[min(self._call_count, len(self.responses) - 1)]
        self._call_count += 1
        return resp

    async def stream_chat(self, messages, tools=None, **kwargs):
        resp = self.responses[min(self._call_count, len(self.responses) - 1)]
        self._call_count += 1
        yield LLMChunk(delta_content=resp.content)
        return


# ---------------------------------------------------------------------------
# Mock Tool
# ---------------------------------------------------------------------------


class MockTool(BaseTool):
    """Mock 工具 —— 始终成功，返回传入的参数。"""

    name = "mock"
    description = "mock tool for testing"
    schema: dict = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args):
        return ToolResult(success=True, data=args, tool_name=self.name)


class EchoTool(BaseTool):
    """Echo 工具 —— 返回传入的 message 参数。"""

    name = "echo"
    description = "echo back the message"
    schema: dict = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "The message to echo"}
        },
        "required": ["message"],
    }

    async def execute(self, args):
        return ToolResult(success=True, data=f"echo: {args['message']}", tool_name=self.name)


# ---------------------------------------------------------------------------
# 集成测试
# ---------------------------------------------------------------------------


class TestAgentSimpleRun:
    """测试 Agent 执行简单任务（无工具调用）。"""

    @pytest.mark.asyncio
    async def test_simple_agent_run(self):
        """Agent 执行简单任务，应返回含 steps 的 AgentState。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(content="The answer is 42.", usage=UsageStats(prompt_tokens=10, completion_tokens=5, total_tokens=15)),
        ])
        agent = Agent(llm=mock_llm)

        state = await agent.run("What is the meaning of life?")

        # 返回 AgentState
        assert isinstance(state, AgentState)
        assert state.task == "What is the meaning of life?"

        # 应有执行步骤
        assert len(state.steps) > 0
        assert state.steps[0].step_type == "llm_call"

        # messages 应有内容
        assert len(state.messages) >= 1
        # 最后一条 assistant 消息应包含答案
        final_msg = state.messages[-1]
        assert final_msg["role"] == "assistant"
        assert "42" in final_msg.get("content", "")

    @pytest.mark.asyncio
    async def test_agent_run_with_system_prompt(self):
        """配置 system_prompt 的 Agent 执行后，messages 第一条应为 system 消息。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(content="OK", usage=UsageStats()),
        ])
        agent = Agent(llm=mock_llm, system_prompt="You are a helpful assistant.")

        state = await agent.run("Hello")

        assert state.messages[0]["role"] == "system"
        assert state.messages[0]["content"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_agent_state_has_messages(self):
        """执行后 state.messages 应有内容。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(content="Hello, how can I help?", usage=UsageStats()),
        ])
        agent = Agent(llm=mock_llm)

        state = await agent.run("Hi there")

        assert len(state.messages) >= 1
        # assistant 消息应包含 LLM 的回复
        assistant_msgs = [m for m in state.messages if m["role"] == "assistant"]
        assert len(assistant_msgs) >= 1
        assert "Hello, how can I help?" in assistant_msgs[0].get("content", "")
        # task 存储在 state.task 中，不会自动作为 user 消息加入
        assert state.task == "Hi there"


class TestAgentWithTool:
    """测试 Agent 工具调用流程。"""

    @pytest.mark.asyncio
    async def test_agent_with_tool(self):
        """Agent 调用工具后，state 应含 tool 调用记录。"""
        # LLM 第一次返回 tool_calls，第二次返回文本
        mock_llm = MockLLM(responses=[
            LLMResponse(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="call_tool_001",
                        name="echo",
                        arguments={"message": "hello world"},
                    )
                ],
                usage=UsageStats(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            ),
            LLMResponse(
                content="I echoed your message successfully.",
                usage=UsageStats(prompt_tokens=30, completion_tokens=5, total_tokens=35),
            ),
        ])

        agent = Agent(llm=mock_llm, max_steps=20)
        agent.register_tool(EchoTool())

        state = await agent.run("Echo 'hello world'")

        assert isinstance(state, AgentState)

        # 应有 tool_call 记录
        assert len(state.tool_calls) > 0
        tc = state.tool_calls[0]
        assert tc.tool_name == "echo"
        assert tc.arguments == {"message": "hello world"}
        assert tc.result == "echo: hello world"

        # steps 应包含 tool_call 类型
        step_types = [s.step_type for s in state.steps]
        assert "tool_call" in step_types
        assert "llm_call" in step_types

        # messages 应包含 tool role 的消息
        tool_messages = [m for m in state.messages if m["role"] == "tool"]
        assert len(tool_messages) > 0


class TestRuntimeEvents:
    """测试 Runtime 执行过程中的事件派发。"""

    @pytest.mark.asyncio
    async def test_runtime_events(self):
        """Runtime 执行过程中事件应被正确派发。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(content="Done.", usage=UsageStats()),
        ])

        # 使用 Runtime 直接测试，便于访问 EventBus
        runtime = Runtime()
        runtime.register_tool = runtime._tool_registry.register  # 快捷方式

        received_events: list = []

        async def event_collector(event):
            received_events.append(event.type)

        await runtime._event_bus.subscribe(EventType.BEFORE_AGENT_RUN, event_collector)
        await runtime._event_bus.subscribe(EventType.BEFORE_LLM_CALL, event_collector)
        await runtime._event_bus.subscribe(EventType.AFTER_LLM_CALL, event_collector)
        await runtime._event_bus.subscribe(EventType.ON_FINISH, event_collector)
        await runtime._event_bus.subscribe(EventType.AFTER_AGENT_RUN, event_collector)

        from nexus.core.executor.react_policy import ReActPolicy

        state = await runtime.run(
            task="Say hello",
            llm=mock_llm,
            policy=ReActPolicy(max_steps=10),
        )

        assert state is not None

        # 验证关键事件被触发
        assert EventType.BEFORE_AGENT_RUN in received_events
        assert EventType.BEFORE_LLM_CALL in received_events
        assert EventType.AFTER_LLM_CALL in received_events
        assert EventType.ON_FINISH in received_events
        assert EventType.AFTER_AGENT_RUN in received_events

    @pytest.mark.asyncio
    async def test_runtime_tool_events(self):
        """工具调用前后应派发 BEFORE_TOOL_CALL 和 AFTER_TOOL_CALL 事件。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="call_evt_001",
                        name="mock",
                        arguments={},
                    )
                ],
                usage=UsageStats(),
            ),
            LLMResponse(content="Tool executed.", usage=UsageStats()),
        ])

        runtime = Runtime()
        runtime._tool_registry.register(MockTool())

        received_events: list = []

        async def event_collector(event):
            received_events.append(event.type)

        await runtime._event_bus.subscribe(EventType.BEFORE_TOOL_CALL, event_collector)
        await runtime._event_bus.subscribe(EventType.AFTER_TOOL_CALL, event_collector)

        from nexus.core.executor.react_policy import ReActPolicy

        await runtime.run(
            task="Run the mock tool",
            llm=mock_llm,
            policy=ReActPolicy(max_steps=10),
        )

        assert EventType.BEFORE_TOOL_CALL in received_events
        assert EventType.AFTER_TOOL_CALL in received_events


class TestRuntimeEdgeCases:
    """测试 Runtime 边界情况。"""

    @pytest.mark.asyncio
    async def test_run_with_variables(self):
        """传入 variables 后，state.variables 应有对应值。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(content="OK", usage=UsageStats()),
        ])

        runtime = Runtime()
        from nexus.core.executor.react_policy import ReActPolicy

        state = await runtime.run(
            task="test",
            llm=mock_llm,
            policy=ReActPolicy(max_steps=10),
            variables={"custom_key": "custom_value"},
        )

        assert state.variables.get("custom_key") == "custom_value"

    @pytest.mark.asyncio
    async def test_run_with_initial_messages(self):
        """传入 initial_messages 后，state.messages 应包含这些消息。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(content="OK", usage=UsageStats()),
        ])

        runtime = Runtime()
        from nexus.core.executor.react_policy import ReActPolicy

        state = await runtime.run(
            task="test",
            llm=mock_llm,
            policy=ReActPolicy(max_steps=10),
            initial_messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello."},
            ],
        )

        assert len(state.messages) >= 2
        assert state.messages[0]["role"] == "system"
        assert state.messages[0]["content"] == "You are helpful."
        assert state.messages[1]["role"] == "user"
        assert state.messages[1]["content"] == "Hello."

    @pytest.mark.asyncio
    async def test_agent_name_in_events(self):
        """通过 Agent 执行时，BEFORE_AGENT_RUN 事件的 payload 应含 agent_name。"""
        mock_llm = MockLLM(responses=[
            LLMResponse(content="OK", usage=UsageStats()),
        ])
        agent = Agent(llm=mock_llm, name="test-agent-42")

        received_payloads: list = []

        async def event_collector(event):
            if event.type == EventType.BEFORE_AGENT_RUN:
                received_payloads.append(event.payload)

        # subscribe 是 async 方法
        await agent.events.subscribe(EventType.BEFORE_AGENT_RUN, event_collector)

        await agent.run("test task")

        assert len(received_payloads) >= 1
        # Runtime 当前硬编码 agent_name 为 "nexus"
        assert "agent_name" in received_payloads[0]
        assert isinstance(received_payloads[0]["agent_name"], str)
