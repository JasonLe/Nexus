"""MiniMax adaptive thinking 与 Anthropic thinking block 解析测试。

覆盖范围
--------
- MiniMaxAnthropicLLM 默认启用 thinking={"type": "adaptive"}
- 可显式传入 thinking 参数覆盖或关闭
- AnthropicLLM._parse_response 处理 thinking block → reasoning_content
- 调用方 kwargs 中的 thinking 优先于实例默认
"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nexus.llm.base import LLMResponse
from nexus.llm.providers.anthropic import AnthropicLLM
from nexus.llm.providers.minimax import MiniMaxAnthropicLLM


def _run(coro):
    """在新事件循环中运行协程，兼容 Python 3.14 严格事件循环策略。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# MiniMaxAnthropicLLM 默认配置
# ---------------------------------------------------------------------------


class TestMiniMaxDefaultThinking:
    """验证 MiniMaxAnthropicLLM 默认启用 adaptive thinking。"""

    def test_default_thinking_class_attribute(self):
        """MiniMax 类默认 _default_thinking 为 {"type": "adaptive"}。"""
        assert MiniMaxAnthropicLLM._default_thinking == {"type": "adaptive"}

    def test_default_anthropic_thinking_disabled(self):
        """原生 AnthropicLLM 默认 _default_thinking 为 None（关闭）。"""
        assert AnthropicLLM._default_thinking is None

    def test_minimax_instance_default_thinking(self):
        """未传 thinking 时，MiniMaxAnthropicLLM 实例 _thinking 为 adaptive。"""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            llm = MiniMaxAnthropicLLM(api_key="test-key")
            try:
                assert llm._thinking == {"type": "adaptive"}
            finally:
                # 关闭异步客户端避免事件循环告警
                try:
                    _run(llm._client.close())
                except Exception:
                    pass

    def test_minimax_explicit_thinking_override(self):
        """显式传 thinking 参数时覆盖默认值。"""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            llm = MiniMaxAnthropicLLM(
                api_key="test-key",
                thinking={"type": "enabled", "budget_tokens": 4096},
            )
            try:
                assert llm._thinking == {"type": "enabled", "budget_tokens": 4096}
            finally:
                try:
                    _run(llm._client.close())
                except Exception:
                    pass

    def test_minimax_thinking_none_uses_class_default(self):
        """显式传 None 时回退到类默认（adaptive），而非关闭。"""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            llm = MiniMaxAnthropicLLM(api_key="test-key", thinking=None)
            try:
                # 传 None 表示"使用默认值"，不是"关闭"。
                # 当前实现选择前者，与 docstring 保持一致。
                assert llm._thinking == {"type": "adaptive"}
            finally:
                try:
                    _run(llm._client.close())
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# AnthropicLLM._parse_response 处理 thinking block
# ---------------------------------------------------------------------------


class TestParseThinkingBlock:
    """测试 _parse_response 从响应中聚合 thinking block 到 reasoning_content。"""

    def _make_response(
        self,
        text: str = "answer text",
        thinking_text: str = "",
        tool_uses: list | None = None,
    ) -> SimpleNamespace:
        """构造模拟的 Anthropic Message 响应。"""
        blocks = []
        if thinking_text:
            blocks.append(SimpleNamespace(type="thinking", thinking=thinking_text))
        if text:
            blocks.append(SimpleNamespace(type="text", text=text))
        for i, tool in enumerate(tool_uses or []):
            blocks.append(SimpleNamespace(
                type="tool_use",
                id=tool.get("id", f"toolu_{i}"),
                name=tool.get("name", "x"),
                input=tool.get("input", {}),
            ))
        return SimpleNamespace(
            content=blocks,
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
            model="test-model",
        )

    def test_text_only(self):
        """仅 text block → reasoning_content 为空。"""
        response = self._make_response(text="hi", thinking_text="")
        result = AnthropicLLM._parse_response(response)
        assert result.content == "hi"
        assert result.reasoning_content == ""

    def test_thinking_only(self):
        """仅 thinking block → reasoning_content 有内容，content 为空。"""
        response = self._make_response(text="", thinking_text="reasoning here")
        result = AnthropicLLM._parse_response(response)
        assert result.content == ""
        assert result.reasoning_content == "reasoning here"

    def test_thinking_and_text(self):
        """thinking + text 同时存在时分别归类。"""
        response = self._make_response(text="answer", thinking_text="thinking text")
        result = AnthropicLLM._parse_response(response)
        assert result.content == "answer"
        assert result.reasoning_content == "thinking text"

    def test_thinking_and_tool_use(self):
        """thinking + tool_use 同时存在时三者都正确解析。"""
        response = self._make_response(
            text="",
            thinking_text="need to call tool",
            tool_uses=[{"id": "t1", "name": "list_dir", "input": {"path": "."}}],
        )
        result = AnthropicLLM._parse_response(response)
        assert result.reasoning_content == "need to call tool"
        assert result.content == ""
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "list_dir"

    def test_empty_thinking_text_skipped(self):
        """thinking block 但内容为空字符串时跳过。"""
        response = self._make_response(text="hi", thinking_text="")
        # 直接构造空 thinking 的场景
        response_empty = SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
            model="m",
        )
        result = AnthropicLLM._parse_response(response_empty)
        assert result.reasoning_content == ""
        assert result.content == ""


# ---------------------------------------------------------------------------
# thinking 参数注入到 SDK 调用参数
# ---------------------------------------------------------------------------


class TestThinkingInjection:
    """验证 thinking 默认值会被注入到 Anthropic SDK 调用参数。"""

    def _capture_chat_params(self, llm: AnthropicLLM) -> dict:
        """mock 掉 SDK client.messages.create，捕获传入的 params。"""
        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                model="m",
            )

        llm._client.messages.create = fake_create
        return captured

    def test_minimax_injects_adaptive_thinking(self):
        """MiniMax 默认调用应注入 thinking={"type": "adaptive"}。"""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            llm = MiniMaxAnthropicLLM(api_key="test-key")
        captured = self._capture_chat_params(llm)

        async def run():
            await llm.chat(messages=[{"role": "user", "content": "hi"}])

        _run(run())
        assert captured.get("thinking") == {"type": "adaptive"}

    def test_anthropic_default_no_thinking(self):
        """原生 AnthropicLLM 默认调用不应注入 thinking。"""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            llm = AnthropicLLM(api_key="test-key")
        captured = self._capture_chat_params(llm)

        async def run():
            await llm.chat(messages=[{"role": "user", "content": "hi"}])

        _run(run())
        assert "thinking" not in captured

    def test_caller_kwargs_thinking_overrides_default(self):
        """调用方 kwargs 传入的 thinking 优先于实例默认。"""
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            llm = MiniMaxAnthropicLLM(api_key="test-key")
        captured = self._capture_chat_params(llm)
        custom_thinking = {"type": "enabled", "budget_tokens": 8192}

        async def run():
            await llm.chat(
                messages=[{"role": "user", "content": "hi"}],
                thinking=custom_thinking,
            )

        _run(run())
        assert captured.get("thinking") == custom_thinking
