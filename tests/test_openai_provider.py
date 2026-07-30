"""OpenAI Provider 测试 —— 覆盖 chat / stream_chat / 错误分类 / 重试 / 超时。

使用 mock AsyncOpenAI 客户端，避免真实 API 调用。
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.core.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMServerError,
)
from nexus.llm.base import LLMChunk, LLMResponse, ToolCall


def _make_chat_response(
    content="Hello!",
    tool_calls=None,
    finish_reason="stop",
    model="gpt-4o-mini",
):
    """构造 mock OpenAI ChatCompletion 响应。"""
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = tool_calls
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    response.model = model
    response.usage = MagicMock(
        prompt_tokens=10, completion_tokens=5, total_tokens=15
    )
    return response


def _make_tool_call_delta(idx, tc_id=None, name=None, arguments=None):
    """构造流式 tool_call delta。"""
    delta = MagicMock()
    delta.index = idx
    delta.id = tc_id
    func = MagicMock()
    func.name = name
    func.arguments = arguments
    delta.function = func
    return delta


class TestOpenAIChat:
    """OpenAILLM.chat() 测试。"""

    @pytest.mark.asyncio
    async def test_chat_basic_text_response(self):
        """基础文本响应解析。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        mock_response = _make_chat_response(content="Hi there")
        llm.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await llm.chat(messages=[{"role": "user", "content": "Hello"}])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hi there"
        assert result.tool_calls == []
        assert result.finish_reason == "stop"
        assert result.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_chat_with_tool_calls(self):
        """tool_calls 解析。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "get_weather"
        tc.function.arguments = '{"city": "Beijing"}'
        mock_response = _make_chat_response(
            content=None, tool_calls=[tc], finish_reason="tool_calls"
        )
        llm.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await llm.chat(
            messages=[{"role": "user", "content": "weather?"}],
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_123"
        assert result.tool_calls[0].name == "get_weather"
        assert result.tool_calls[0].arguments == {"city": "Beijing"}

    @pytest.mark.asyncio
    async def test_chat_content_none_returns_empty_string(self):
        """content 为 None 时返回空字符串。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        mock_response = _make_chat_response(content=None)
        llm.client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await llm.chat(messages=[{"role": "user", "content": "hi"}])
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_chat_auth_error_not_retried(self):
        """401 鉴权错误立即抛出，不重试。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test", max_retries=3)

        error = Exception("Unauthorized")
        error.status_code = 401
        llm.client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(LLMAuthError):
            await llm.chat(messages=[{"role": "user", "content": "hi"}])

        # 仅调用 1 次（不重试）
        assert llm.client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_chat_rate_limit_retried(self):
        """429 限流错误触发重试，最终成功。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test", max_retries=2)

        error = Exception("Rate limited")
        error.status_code = 429
        mock_response = _make_chat_response(content="OK")

        llm.client.chat.completions.create = AsyncMock(
            side_effect=[error, mock_response]
        )

        with patch("nexus.llm._retry.asyncio.sleep", new_callable=AsyncMock):
            result = await llm.chat(messages=[{"role": "user", "content": "hi"}])

        assert result.content == "OK"
        assert llm.client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    async def test_chat_server_error_retried_then_fail(self):
        """5xx 服务端错误重试耗尽后抛出。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test", max_retries=2)

        error = Exception("Internal error")
        error.status_code = 500
        llm.client.chat.completions.create = AsyncMock(side_effect=error)

        with patch("nexus.llm._retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(LLMServerError):
                await llm.chat(messages=[{"role": "user", "content": "hi"}])

        # 首次 + 2 次重试 = 3 次
        assert llm.client.chat.completions.create.call_count == 3

    @pytest.mark.asyncio
    async def test_chat_timeout(self):
        """超时异常被包装为 LLMTimeoutError。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test", max_retries=0)
        llm.client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        from nexus.core.exceptions import LLMTimeoutError

        with pytest.raises(LLMTimeoutError):
            await llm.chat(messages=[{"role": "user", "content": "hi"}])


class TestOpenAIStreamChat:
    """OpenAILLM.stream_chat() 测试。"""

    @pytest.mark.asyncio
    async def test_stream_basic_text(self):
        """流式文本增量。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")

        # 构造流式 chunks
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = "Hello"
        chunk1.choices[0].delta.tool_calls = None
        chunk1.choices[0].finish_reason = None

        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = " world"
        chunk2.choices[0].delta.tool_calls = None
        chunk2.choices[0].finish_reason = "stop"

        async def _async_iter():
            for c in [chunk1, chunk2]:
                yield c

        llm.client.chat.completions.create = AsyncMock(return_value=_async_iter())

        chunks = []
        async for chunk in llm.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        # 应得到两个增量 chunk
        assert len(chunks) == 2
        assert chunks[0].delta_content == "Hello"
        assert chunks[1].delta_content == " world"
        assert chunks[1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_stream_tool_calls_aggregation(self):
        """流式 tool_calls 按 index 聚合。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")

        # 第一个 chunk：tool_call 开始（id + name）
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        chunk1.choices[0].delta.content = None
        tc_delta1 = _make_tool_call_delta(0, tc_id="call_1", name="search")
        chunk1.choices[0].delta.tool_calls = [tc_delta1]
        chunk1.choices[0].finish_reason = None

        # 第二个 chunk：arguments 增量
        chunk2 = MagicMock()
        chunk2.choices = [MagicMock()]
        chunk2.choices[0].delta.content = None
        tc_delta2 = _make_tool_call_delta(0, arguments='{"q": "')
        chunk2.choices[0].delta.tool_calls = [tc_delta2]
        chunk2.choices[0].finish_reason = None

        # 第三个 chunk：arguments 增量完成
        chunk3 = MagicMock()
        chunk3.choices = [MagicMock()]
        chunk3.choices[0].delta.content = None
        tc_delta3 = _make_tool_call_delta(0, arguments='test"}')
        chunk3.choices[0].delta.tool_calls = [tc_delta3]
        chunk3.choices[0].finish_reason = "tool_calls"

        async def _async_iter():
            for c in [chunk1, chunk2, chunk3]:
                yield c

        llm.client.chat.completions.create = AsyncMock(return_value=_async_iter())

        chunks = []
        async for chunk in llm.stream_chat(messages=[{"role": "user", "content": "search"}]):
            chunks.append(chunk)

        # 最后一个 chunk 应有完整 tool_call
        last = chunks[-1]
        assert len(last.delta_tool_calls) == 1
        assert last.delta_tool_calls[0].id == "call_1"
        assert last.delta_tool_calls[0].name == "search"
        assert last.delta_tool_calls[0].arguments == {"q": "test"}

    @pytest.mark.asyncio
    async def test_stream_skips_empty_choices(self):
        """空 choices 的 chunk 被跳过。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")

        empty_chunk = MagicMock()
        empty_chunk.choices = []

        real_chunk = MagicMock()
        real_chunk.choices = [MagicMock()]
        real_chunk.choices[0].delta.content = "data"
        real_chunk.choices[0].delta.tool_calls = None
        real_chunk.choices[0].finish_reason = "stop"

        async def _async_iter():
            for c in [empty_chunk, real_chunk]:
                yield c

        llm.client.chat.completions.create = AsyncMock(return_value=_async_iter())

        chunks = []
        async for chunk in llm.stream_chat(messages=[{"role": "user", "content": "hi"}]):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].delta_content == "data"


class TestOpenAIErrorConversion:
    """OpenAI _convert_exception 错误分类测试。"""

    def test_401_to_auth_error(self):
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        e = Exception("Unauthorized")
        e.status_code = 401
        result = llm._convert_exception(e, "chat")
        assert isinstance(result, LLMAuthError)

    def test_403_to_auth_error(self):
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        e = Exception("Forbidden")
        e.status_code = 403
        result = llm._convert_exception(e, "chat")
        assert isinstance(result, LLMAuthError)

    def test_429_to_rate_limit_error(self):
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        e = Exception("Too many requests")
        e.status_code = 429
        result = llm._convert_exception(e, "chat")
        assert isinstance(result, LLMRateLimitError)

    def test_500_to_server_error(self):
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        e = Exception("Internal")
        e.status_code = 500
        result = llm._convert_exception(e, "chat")
        assert isinstance(result, LLMServerError)

    def test_503_to_server_error(self):
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        e = Exception("Unavailable")
        e.status_code = 503
        result = llm._convert_exception(e, "chat")
        assert isinstance(result, LLMServerError)

    def test_timeout_to_timeout_error(self):
        from nexus.core.exceptions import LLMTimeoutError
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        result = llm._convert_exception(asyncio.TimeoutError(), "chat")
        assert isinstance(result, LLMTimeoutError)

    def test_unknown_to_llm_error(self):
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        result = llm._convert_exception(ValueError("bad"), "chat")
        assert isinstance(result, LLMError)
        assert not isinstance(result, LLMAuthError)


class TestOpenAIInit:
    """OpenAILLM 构造函数测试。"""

    def test_timeout_passed_to_client(self):
        """timeout 参数传入 AsyncOpenAI 客户端。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test", timeout=120.0)
        assert llm._timeout == 120.0

    def test_context_window_tokens_default_zero(self):
        """默认 context_window_tokens=0（不截断）。"""
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test")
        assert llm.context_window_tokens == 0

    def test_context_window_tokens_custom(self):
        from nexus.llm.providers.openai import OpenAILLM

        llm = OpenAILLM(api_key="sk-test", context_window_tokens=128000)
        assert llm.context_window_tokens == 128000
