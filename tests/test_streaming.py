"""流式输出测试。

覆盖范围
--------
- LLMChunk.delta_reasoning 字段存在性与赋值
- EventType.LLM_CHUNK 事件类型存在
- NexusConfig.stream 默认值与可修改
- OpenAI provider stream_chat 解析 delta.reasoning_content
- Anthropic provider stream_chat 解析 thinking_delta 事件
- Runtime 流式路径派发 LLM_CHUNK 事件
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nexus.llm.base import BaseLLM, LLMChunk, LLMResponse, UsageStats
from nexus.core.event.event_types import EventType
from nexus.core.event.types import Event
from nexus.cli.config import NexusConfig


# ---------------------------------------------------------------------------
# 数据结构 / 枚举 / 配置 单元测试
# ---------------------------------------------------------------------------


class TestLLMChunk:
    """LLMChunk 数据结构测试。"""

    def test_delta_reasoning_field_exists(self):
        """delta_reasoning 字段存在且默认为空字符串。"""
        chunk = LLMChunk()
        assert chunk.delta_reasoning == ""

    def test_delta_reasoning_can_be_set(self):
        """delta_reasoning 可以设置。"""
        chunk = LLMChunk(delta_reasoning="thinking...")
        assert chunk.delta_reasoning == "thinking..."

    def test_delta_content_default_empty(self):
        """delta_content 默认为空字符串。"""
        chunk = LLMChunk()
        assert chunk.delta_content == ""

    def test_finish_reason_default_none(self):
        """finish_reason 默认为 None（传输中）。"""
        chunk = LLMChunk()
        assert chunk.finish_reason is None


class TestEventType:
    """事件类型测试。"""

    def test_llm_chunk_event_exists(self):
        """LLM_CHUNK 事件类型存在。"""
        assert EventType.LLM_CHUNK is not None

    def test_llm_chunk_differs_from_other_events(self):
        """LLM_CHUNK 与 BEFORE_LLM_CALL / AFTER_LLM_CALL 不同。"""
        assert EventType.LLM_CHUNK != EventType.BEFORE_LLM_CALL
        assert EventType.LLM_CHUNK != EventType.AFTER_LLM_CALL

    def test_llm_chunk_is_enum_member(self):
        """LLM_CHUNK 是 EventType 枚举成员。"""
        assert isinstance(EventType.LLM_CHUNK, EventType)


class TestConfig:
    """配置测试。"""

    def test_stream_default_true(self):
        """stream 默认为 True。"""
        config = NexusConfig()
        assert config.stream is True

    def test_stream_can_be_false(self):
        """stream 可设为 False。"""
        config = NexusConfig()
        config.stream = False
        assert config.stream is False


# ---------------------------------------------------------------------------
# OpenAI provider stream_chat 解析 reasoning_content
# ---------------------------------------------------------------------------


def _make_openai_stream_chunk(
    content=None,
    reasoning_content=None,
    tool_calls=None,
    finish_reason=None,
):
    """构造 mock OpenAI 流式 chunk。

    显式设置 reasoning_content（None 或字符串），避免 MagicMock 自动创建
    属性导致 hasattr 判断失真。
    """
    delta = MagicMock()
    delta.content = content
    delta.reasoning_content = reasoning_content
    delta.tool_calls = tool_calls

    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason

    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


class TestOpenAIStreamReasoning:
    """OpenAI provider 解析 reasoning_content 测试。"""

    @pytest.mark.asyncio
    async def test_openai_stream_parses_reasoning_content(self):
        """OpenAI stream_chat 解析 delta.reasoning_content 并填入 delta_reasoning。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")

        # chunk1：纯推理内容（o1/o3 系列的 reasoning_content 字段）
        chunk1 = _make_openai_stream_chunk(
            content=None,
            reasoning_content="Let me think about this step by step.",
        )
        # chunk2：正式回复内容
        chunk2 = _make_openai_stream_chunk(
            content="The answer is 42.",
            reasoning_content=None,
            finish_reason="stop",
        )

        async def _async_iter():
            for c in [chunk1, chunk2]:
                yield c

        llm.client.chat.completions.create = AsyncMock(return_value=_async_iter())

        chunks = []
        async for chunk in llm.stream_chat(
            messages=[{"role": "user", "content": "What is the answer?"}],
        ):
            chunks.append(chunk)

        # 应得到两个 chunk
        assert len(chunks) == 2

        # 第一个 chunk：推理内容
        assert chunks[0].delta_reasoning == "Let me think about this step by step."
        assert chunks[0].delta_content == ""

        # 第二个 chunk：正式回复
        assert chunks[1].delta_content == "The answer is 42."
        assert chunks[1].delta_reasoning == ""
        assert chunks[1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_openai_stream_reasoning_and_content_in_same_chunk(self):
        """同一 chunk 可同时包含 reasoning 和 content。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")

        chunk = _make_openai_stream_chunk(
            content="Answer",
            reasoning_content="Reasoning",
        )

        async def _async_iter():
            yield chunk

        llm.client.chat.completions.create = AsyncMock(return_value=_async_iter())

        chunks = []
        async for c in llm.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(c)

        assert len(chunks) == 1
        assert chunks[0].delta_reasoning == "Reasoning"
        assert chunks[0].delta_content == "Answer"


# ---------------------------------------------------------------------------
# Anthropic provider stream_chat 解析 thinking_delta
# ---------------------------------------------------------------------------


class _MockAnthropicStreamCtx:
    """Mock Anthropic messages.stream() 返回的 async context manager。

    Anthropic SDK 的 stream() 返回一个 async context manager，
    __aenter__ 返回一个 async iterator of events。
    """

    def __init__(self, events):
        self._events = list(events)

    async def __aenter__(self):
        events = self._events

        async def _gen():
            for e in events:
                yield e

        return _gen()

    async def __aexit__(self, *args):
        return False


class TestAnthropicStreamThinking:
    """Anthropic provider 解析 thinking_delta 测试。"""

    @pytest.mark.asyncio
    async def test_anthropic_stream_parses_thinking_delta(self):
        """Anthropic stream_chat 解析 thinking_delta 事件并填入 delta_reasoning。"""
        from nexus.llm.providers.anthropic import AnthropicLLM

        llm = AnthropicLLM(api_key="sk-ant-test")

        # 构造 Anthropic stream 事件序列
        events = [
            # 思考链增量
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="thinking_delta", thinking="Analyzing the question..."),
            ),
            # 正式文本回复增量
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="The answer is 42."),
            ),
            # 消息结束
            SimpleNamespace(
                type="message_stop",
                message=SimpleNamespace(stop_reason="end_turn"),
            ),
        ]

        # 替换 _client 为 MagicMock，便于挂载 stream
        llm._client = MagicMock()
        llm._client.messages.stream = MagicMock(
            return_value=_MockAnthropicStreamCtx(events),
        )

        chunks = []
        async for chunk in llm.stream_chat(
            messages=[{"role": "user", "content": "What is the answer?"}],
        ):
            chunks.append(chunk)

        # 应至少得到思考链 chunk 和文本 chunk
        reasoning_chunks = [c for c in chunks if c.delta_reasoning]
        content_chunks = [c for c in chunks if c.delta_content]

        assert len(reasoning_chunks) >= 1
        assert reasoning_chunks[0].delta_reasoning == "Analyzing the question..."

        assert len(content_chunks) >= 1
        assert content_chunks[0].delta_content == "The answer is 42."

        # 最后应有 finish_reason
        finish_chunks = [c for c in chunks if c.finish_reason]
        assert len(finish_chunks) >= 1
        assert finish_chunks[-1].finish_reason == "stop"  # end_turn → stop

    @pytest.mark.asyncio
    async def test_anthropic_stream_text_only(self):
        """无 thinking_delta 时 delta_reasoning 始终为空。"""
        from nexus.llm.providers.anthropic import AnthropicLLM

        llm = AnthropicLLM(api_key="sk-ant-test")

        events = [
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text="Hello!"),
            ),
            SimpleNamespace(
                type="message_stop",
                message=SimpleNamespace(stop_reason="end_turn"),
            ),
        ]

        llm._client = MagicMock()
        llm._client.messages.stream = MagicMock(
            return_value=_MockAnthropicStreamCtx(events),
        )

        chunks = []
        async for chunk in llm.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
        ):
            chunks.append(chunk)

        # 所有 chunk 的 delta_reasoning 应为空
        for c in chunks:
            assert c.delta_reasoning == ""

        # 至少有一个 content chunk
        content_chunks = [c for c in chunks if c.delta_content]
        assert len(content_chunks) == 1
        assert content_chunks[0].delta_content == "Hello!"


# ---------------------------------------------------------------------------
# Runtime 流式路径派发 LLM_CHUNK 事件
# ---------------------------------------------------------------------------


class _StreamingMockLLM(BaseLLM):
    """流式 Mock LLM —— yield 多个含 reasoning/content 的 chunk。"""

    def __init__(self):
        super().__init__()
        self._call_count = 0

    async def chat(self, messages, tools=None, **kwargs):
        return LLMResponse(
            content="The answer is 42.",
            usage=UsageStats(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    async def stream_chat(self, messages, tools=None, **kwargs):
        self._call_count += 1
        yield LLMChunk(delta_reasoning="Let me think...")
        yield LLMChunk(delta_content="The answer is ")
        yield LLMChunk(delta_content="42.", finish_reason="stop")


class TestRuntimeStreaming:
    """Runtime 流式路径测试。"""

    @pytest.mark.asyncio
    async def test_runtime_dispatches_llm_chunk_events(self):
        """Runtime 流式路径派发 LLM_CHUNK 事件，payload 含 delta_reasoning/delta_content。"""
        from nexus.core.runtime.runtime import Runtime
        from nexus.core.executor.react_policy import ReActPolicy

        mock_llm = _StreamingMockLLM()
        runtime = Runtime()

        received_chunks: list[Event] = []

        async def chunk_collector(event: Event):
            received_chunks.append(event)

        await runtime._event_bus.subscribe(EventType.LLM_CHUNK, chunk_collector)

        state = await runtime.run(
            task="What is the answer?",
            llm=mock_llm,
            policy=ReActPolicy(max_steps=10),
            variables={"_stream": True},
        )

        # 应收到 3 个 LLM_CHUNK 事件（reasoning + 2 个 content）
        assert len(received_chunks) == 3

        # 第一个：推理
        assert received_chunks[0].payload["delta_reasoning"] == "Let me think..."
        assert received_chunks[0].payload["delta_content"] == ""

        # 第二个：content 增量
        assert received_chunks[1].payload["delta_content"] == "The answer is "
        assert received_chunks[1].payload["delta_reasoning"] == ""

        # 第三个：content 增量 + finish_reason
        assert received_chunks[2].payload["delta_content"] == "42."
        assert received_chunks[2].payload["finish_reason"] == "stop"

        # 验证聚合后的 assistant 消息内容正确
        assistant_msgs = [m for m in state.messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) >= 1
        assert "42" in assistant_msgs[-1].get("content", "")

    @pytest.mark.asyncio
    async def test_runtime_non_stream_no_llm_chunk_events(self):
        """非流式模式（_stream=False）不应派发 LLM_CHUNK 事件。"""
        from nexus.core.runtime.runtime import Runtime
        from nexus.core.executor.react_policy import ReActPolicy

        mock_llm = _StreamingMockLLM()
        runtime = Runtime()

        received_chunks: list[Event] = []

        async def chunk_collector(event: Event):
            received_chunks.append(event)

        await runtime._event_bus.subscribe(EventType.LLM_CHUNK, chunk_collector)

        await runtime.run(
            task="test",
            llm=mock_llm,
            policy=ReActPolicy(max_steps=10),
            variables={"_stream": False},
        )

        # 非流式模式不应派发 LLM_CHUNK 事件
        assert len(received_chunks) == 0
