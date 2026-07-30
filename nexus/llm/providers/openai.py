"""OpenAI LLM Provider —— 基于 openai SDK 的 BaseLLM 实现。

设计思路
--------
包装 openai Python SDK，将 OpenAI 的请求/响应格式映射为 Nexus 统一的数据结构。
Provider 层负责：
1. SDK 调用和连接管理（含超时控制）
2. 响应格式转换（OpenAI API -> LLMResponse/LLMChunk/UsageStats/ToolCall）
3. 错误处理（将 openai 异常包装为 LLMError 子类，区分可重试/不可重试）
4. 重试（指数退避，仅对 LLMRetryableError 子类重试）
5. 参数透传（兼容未来需要的高级参数如 temperature/top_p/max_tokens）

流式聚合策略
------------
stream_chat() 支持 tool calling 的流式场景。
OpenAI 的流式 tool calling 会将 tool_choice 分散到多个 chunk 中，
同一个 tool_call 可能由多个 LLMChunk 共同构成（名称/参数分别传输）。
本实现采用增量合并：按 tool_call.index 聚合 delta，每个 chunk yield 合并后的状态。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from nexus.core.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMRetryableError,
    LLMServerError,
    LLMTimeoutError,
)
from nexus.llm._retry import with_retry
from nexus.llm.base import BaseLLM, LLMChunk, LLMResponse, ToolCall, UsageStats
from nexus.logging import get_logger

logger = get_logger(__name__)

# 默认请求超时（秒）
DEFAULT_TIMEOUT = 60.0


class OpenAILLM(BaseLLM):
    """OpenAI LLM Provider —— 封装 openai.AsyncOpenAI SDK。

    通过 AsyncOpenAI 客户端与 OpenAI API 通信，将原生请求/响应格式
    转换为 Nexus 统一的 LLMResponse / LLMChunk 数据结构。

    Parameters
    ----------
    api_key : str | None
        OpenAI API 密钥，默认从环境变量 ``OPENAI_API_KEY`` 读取。
    base_url : str | None
        自定义 API endpoint，用于代理或兼容网关（如 LiteLLM）。
    model : str
        默认模型名称，默认为 ``"gpt-4o-mini"``。
    timeout : float
        请求超时（秒），默认 60。
    max_retries : int
        最大重试次数（仅对可重试错误重试），默认 3。
    **kwargs : Any
        透传给 ``AsyncOpenAI`` 构造函数的其他参数。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        context_window_tokens: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__()

        self.model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self.context_window_tokens = context_window_tokens

        # 若未显式提供 api_key，从环境变量 OPENAI_API_KEY 读取
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_key:
            logger.warning(
                "No API key provided and OPENAI_API_KEY env var is not set"
            )

        client_kwargs: dict[str, Any] = {"timeout": timeout}
        if resolved_key:
            client_kwargs["api_key"] = resolved_key
        if base_url:
            client_kwargs["base_url"] = base_url
        client_kwargs.update(kwargs)

        self.client = AsyncOpenAI(**client_kwargs)

    # ------------------------------------------------------------------
    # 错误转换
    # ------------------------------------------------------------------

    def _convert_exception(self, e: Exception, operation: str) -> LLMError:
        """将 OpenAI SDK 异常转换为 Nexus LLMError 子类。

        根据 HTTP 状态码 / 异常类型区分可重试与不可重试：
        - 401/403 → LLMAuthError（不可重试）
        - 429     → LLMRateLimitError（可重试）
        - 5xx     → LLMServerError（可重试）
        - 超时    → LLMTimeoutError（可重试）
        - 其他    → LLMError（不可重试）
        """
        msg = f"OpenAI {operation} failed: {e}"

        # OpenAI SDK 的 APIStatusError 携带 status_code
        status_code = getattr(e, "status_code", None)

        if status_code is not None:
            if status_code in (401, 403):
                return LLMAuthError(msg)
            if status_code == 429:
                return LLMRateLimitError(msg)
            if 500 <= status_code < 600:
                return LLMServerError(msg)
        # asyncio 超时
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            return LLMTimeoutError(msg)
        # 兜底为不可重试的普通 LLMError
        return LLMError(msg)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送对话消息并获取完整响应。

        调用 OpenAI Chat Completions API（非流式），解析完整响应后
        返回聚合的 ``LLMResponse``。支持指数退避重试（仅对可重试错误）。

        Parameters
        ----------
        messages : list[dict[str, Any]]
            对话消息列表，OpenAI 兼容格式。
        tools : list[dict[str, Any]] | None
            可用工具 schema 列表，传入时自动设置 ``tool_choice="auto"``。
        **kwargs : Any
            透传给 API 的其他参数（temperature / max_tokens / top_p 等）。

        Returns
        -------
        LLMResponse
            包含 content、tool_calls、usage、finish_reason 的完整响应。

        Raises
        ------
        LLMError
            API 通信失败、鉴权失败或触发限流时抛出。
        """
        # 构建 API 请求参数
        # 先按 context window 截断历史消息
        messages = self._maybe_truncate_messages(messages)
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }

        if tools is not None:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # kwargs 可覆盖已设置的参数（允许调用方自定义 tool_choice 等）
        params.update(kwargs)

        async def _do_call() -> Any:
            try:
                return await self.client.chat.completions.create(**params)
            except Exception as e:
                raise self._convert_exception(e, "chat") from e

        try:
            response = await with_retry(
                _do_call,
                max_retries=self._max_retries,
                operation_name="OpenAI chat",
            )
        except LLMError:
            logger.error(
                "OpenAI chat API call failed",
                extra={"model": self.model},
                exc_info=True,
            )
            raise

        choice = response.choices[0]

        # 转换文本内容：content 可能为 None（仅有 tool_calls 时）
        content: str = choice.message.content or ""

        # 转换 tool_calls：OpenAI 原生格式 -> Nexus ToolCall 列表
        tool_calls: list[ToolCall] = []
        if choice.message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=(
                        json.loads(tc.function.arguments)
                        if tc.function.arguments else {}
                    ),
                )
                for tc in choice.message.tool_calls
            ]

        # 提取 token 使用统计
        usage = UsageStats()
        if response.usage:
            usage = UsageStats(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=response.model,
            finish_reason=choice.finish_reason or "",
            raw_response=response,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """流式对话 —— 逐步返回生成内容。

        通过 ``stream=True`` 模式调用 OpenAI Chat Completions API，
        逐个 yield ``LLMChunk``，实现打字机式输出。

        支持流式 tool calling：当多个 chunk 共同构成同一 tool_call 时，
        按 ``tool_call.index`` 增量聚合 arguments，最终在每个 chunk 中
        返回当前累积状态的 ``ToolCall`` 列表。

        Parameters
        ----------
        messages : list[dict[str, Any]]
            对话消息列表。
        tools : list[dict[str, Any]] | None
            可用工具 schema 列表，传入时自动设置 ``tool_choice="auto"``。
        **kwargs : Any
            透传给 API 的其他参数。

        Yields
        ------
        LLMChunk
            逐步返回增量内容，每个 chunk 包含 delta_content 和/或
            合并后的 delta_tool_calls。

        Raises
        ------
        LLMError
            API 通信失败、鉴权失败或触发限流时抛出。
        """
        # 构建 API 请求参数（stream=True 启用流式模式）
        messages = self._maybe_truncate_messages(messages)
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }

        if tools is not None:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # kwargs 可覆盖已设置的参数
        params.update(kwargs)

        async def _open_stream() -> Any:
            try:
                return await self.client.chat.completions.create(**params)
            except Exception as e:
                raise self._convert_exception(e, "stream") from e

        try:
            stream = await with_retry(
                _open_stream,
                max_retries=self._max_retries,
                operation_name="OpenAI stream",
            )
        except LLMError:
            logger.error(
                "OpenAI stream API call failed",
                extra={"model": self.model},
                exc_info=True,
            )
            raise

        # 维护待聚合的 tool_call 状态：key = tool_call.index
        pending_tool_calls: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            # 空 chunk（某些 proxy 可能会产生），跳过
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            finish_reason: str | None = chunk.choices[0].finish_reason

            delta_content: str = delta.content or ""

            # 增量合并 tool_calls
            delta_tool_calls: list[ToolCall] = []
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    # 新 tool_call：创建 pending entry
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }

                    entry = pending_tool_calls[idx]
                    # 首次出现的 chunk 携带 tool_call id
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    # function.name 通常只出现一次
                    if tc_delta.function and tc_delta.function.name:
                        entry["name"] = tc_delta.function.name
                    # function.arguments 为增量 JSON 片段，需要拼接
                    if tc_delta.function and tc_delta.function.arguments:
                        entry["arguments"] += tc_delta.function.arguments

                # 将当前所有已完成聚合的 pending tool_calls 转为 ToolCall
                # 注意：流式传输中 arguments 可能是部分 JSON（尚未完整），
                # json.loads 会抛出 JSONDecodeError，此时用空 dict 占位。
                delta_tool_calls: list[ToolCall] = []
                for v in pending_tool_calls.values():
                    if not v["id"]:
                        continue  # 仅输出已获取 id 的 tool_call
                    parsed_args: dict[str, Any] = {}
                    if v["arguments"]:
                        try:
                            parsed_args = json.loads(v["arguments"])
                        except json.JSONDecodeError:
                            # arguments 尚未接收完整，暂时传空 dict
                            pass
                    delta_tool_calls.append(
                        ToolCall(
                            id=v["id"],
                            name=v["name"],
                            arguments=parsed_args,
                        )
                    )

            yield LLMChunk(
                delta_content=delta_content,
                delta_tool_calls=delta_tool_calls,
                finish_reason=finish_reason,
            )
