"""Anthropic LLM Provider —— 基于 Anthropic SDK 的标准 Provider 实现。

设计思路
--------
使用 anthropic Python SDK 对接 Anthropic Messages API。
Provider 层负责：
1. 连接管理（api_key + base_url）
2. 格式映射（Anthropic SDK 响应 → Nexus LLMResponse / LLMChunk）
3. Tool calling 转换（Anthropic tool_use block ↔ Nexus ToolCall）
4. 错误处理（Anthropic 异常 → LLMError）

此类是 Anthropic 生态的基类——任何使用 Anthropic API 格式的 provider
（如 MiniMax）只需继承此类并覆写构造函数中的默认值即可。

Anthropic ↔ Nexus 格式映射
---------------------------

Messages 转换（Nexus → Anthropic）:
  system role 消息 → Anthropic 的 system 参数
  assistant tool_calls → content 中的 tool_use block
  role="tool" 消息 → role="user" + tool_result block（连续 tool result 合并）

响应转换（Anthropic → Nexus）:
  Anthropic text block      → LLMResponse.content
  Anthropic tool_use block  → ToolCall(id, name, arguments=block.input)
  Anthropic stop_reason     → LLMResponse.finish_reason:
    "end_turn"              → "stop"
    "tool_use"              → "tool_calls"
    "max_tokens"            → "length"
    "stop_sequence"         → "stop"
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from nexus.core.exceptions import LLMError
from nexus.llm.base import BaseLLM, LLMChunk, LLMResponse, ToolCall, UsageStats
from nexus.logging import get_logger

logger = get_logger(__name__)


class AnthropicLLM(BaseLLM):
    """Anthropic LLM Provider —— 基于 Anthropic SDK 的标准实现。

    对接 Anthropic Messages API，支持 chat() 和 stream_chat()。
    子类可覆写 ``_env_key``、``_default_model``、``_default_base_url`` 来适配
    其他兼容 Anthropic API 格式的服务（如 MiniMax）。

    Parameters
    ----------
    api_key : str | None
        API 密钥。默认从环境变量 ``ANTHROPIC_API_KEY`` 读取。
    base_url : str | None
        自定义 API 端点。默认使用 Anthropic 标准端点。
    model : str
        模型名称，默认 ``claude-sonnet-4-20250514``。
    **kwargs : Any
        透传给 ``AsyncAnthropic`` 构造函数的额外参数。

    使用示例
    --------

    >>> from nexus.llm.providers.anthropic import AnthropicLLM
    >>>
    >>> llm = AnthropicLLM(model="claude-sonnet-4-20250514")
    >>> response = await llm.chat(
    ...     messages=[{"role": "user", "content": "Hello!"}],
    ... )
    """

    # 子类可覆写的默认值
    _env_key: str = "ANTHROPIC_API_KEY"
    _default_model: str = "claude-sonnet-4-20250514"
    _default_base_url: str | None = None  # None = 使用 SDK 默认

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.model = model or self._default_model

        # api_key 读取顺序：显式传入 > 环境变量
        resolved_key = api_key or os.getenv(self._env_key)
        if not resolved_key:
            logger.warning(
                "No API key provided and %s env var is not set", self._env_key
            )

        # base_url 读取顺序：显式传入 > 类默认 > SDK 默认
        effective_base_url = base_url or self._default_base_url

        client_kwargs: dict[str, Any] = {}
        if resolved_key:
            client_kwargs["api_key"] = resolved_key
        if effective_base_url:
            client_kwargs["base_url"] = effective_base_url
        client_kwargs.update(kwargs)

        self._client = AsyncAnthropic(**client_kwargs)
        logger.info(
            "%s initialized: model=%s, base_url=%s",
            type(self).__name__,
            self.model,
            effective_base_url or "(default)",
        )

    # ------------------------------------------------------------------
    # chat()
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送对话消息并获取完整响应。

        内部完成 Nexus → Anthropic 格式转换并调用 Anthropic SDK。
        """
        try:
            # 分离 system prompt 并转换为 Anthropic messages 格式
            system_prompt, anthropic_messages = self._split_system_messages(messages)

            params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": kwargs.pop("max_tokens", 4096),
                "messages": anthropic_messages,
            }

            if system_prompt:
                params["system"] = system_prompt

            if tools:
                params["tools"] = self._convert_tools_to_anthropic(tools)

            params.update(kwargs)

            response = await self._client.messages.create(**params)

            return self._parse_response(response)

        except Exception as e:
            logger.error(
                "%s API call failed",
                type(self).__name__,
                extra={"model": self.model},
                exc_info=True,
            )
            raise LLMError(f"{type(self).__name__} API call failed: {e}") from e

    # ------------------------------------------------------------------
    # stream_chat()
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """流式对话 —— 逐步返回 LLM 生成内容和工具调用增量。"""
        try:
            system_prompt, anthropic_messages = self._split_system_messages(messages)

            params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": kwargs.pop("max_tokens", 4096),
                "messages": anthropic_messages,
                "stream": True,
            }

            if system_prompt:
                params["system"] = system_prompt

            if tools:
                params["tools"] = self._convert_tools_to_anthropic(tools)

            params.update(kwargs)

            accumulated_text = ""
            pending_tool_calls: dict[int, dict[str, Any]] = {}
            final_stop_reason: str | None = None

            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            pending_tool_calls[event.index] = {
                                "id": block.id,
                                "name": block.name,
                                "arguments": "",
                            }
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            accumulated_text += delta.text
                        elif delta.type == "input_json_delta":
                            if event.index in pending_tool_calls:
                                pending_tool_calls[event.index]["arguments"] += (
                                    delta.partial_json
                                )
                    elif event.type == "content_block_stop":
                        pass
                    elif event.type == "message_stop":
                        if hasattr(event, "message") and event.message:
                            final_stop_reason = self._map_stop_reason(
                                event.message.stop_reason
                            )

                    delta_tool_calls: list[ToolCall] = []
                    for tc_data in pending_tool_calls.values():
                        if tc_data["name"]:
                            delta_tool_calls.append(ToolCall(
                                id=tc_data["id"],
                                name=tc_data["name"],
                                arguments={},
                            ))

                    yield LLMChunk(
                        delta_content=accumulated_text,
                        delta_tool_calls=delta_tool_calls,
                        finish_reason=final_stop_reason,
                    )

        except Exception as e:
            logger.error(
                "%s stream_chat failed",
                type(self).__name__,
                extra={"model": self.model},
                exc_info=True,
            )
            raise LLMError(f"{type(self).__name__} stream_chat failed: {e}") from e

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _split_system_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """将 Nexus messages 分离为 system prompt 和 Anthropic 消息列表。

        Anthropic API 要求 system prompt 作为独立参数传入，
        而非放在 messages 中。此方法完成提取和转换。

        Runtime 内部使用 OpenAI 格式存储消息（tool_calls 作为顶层字段、
        tool result 用 role="tool"），但 Anthropic API 要求：
        - assistant 的 tool_calls → content 中的 tool_use block
        - tool result (role="tool") → role="user" + tool_result block

        本方法负责上述转换，并合并连续的 tool result 到同一个 user 消息。

        Returns
        -------
        tuple[str | None, list[dict]]
            (system_prompt, anthropic_messages)
        """
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")

            # system 消息 → 提取到 system_prompt 参数
            if role == "system":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    system_parts.append(content)
                continue

            # tool result (OpenAI role="tool") → Anthropic role="user" + tool_result block
            if role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                content = msg.get("content", "")
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": str(content) if content is not None else "",
                }
                # 合并到上一个 user 消息（若它也是 tool_result 合并消息）
                if (
                    anthropic_messages
                    and anthropic_messages[-1]["role"] == "user"
                    and isinstance(anthropic_messages[-1]["content"], list)
                    and anthropic_messages[-1]["content"]
                    and anthropic_messages[-1]["content"][0].get("type") == "tool_result"
                ):
                    anthropic_messages[-1]["content"].append(tool_result_block)
                else:
                    anthropic_messages.append({
                        "role": "user",
                        "content": [tool_result_block],
                    })
                continue

            # assistant 消息 —— 转换 tool_calls（OpenAI）→ tool_use block（Anthropic）
            if role == "assistant":
                content_blocks: list[dict[str, Any]] = []

                # 文本内容
                text_content = msg.get("content")
                if text_content:
                    content_blocks.append({"type": "text", "text": text_content})

                # tool_calls → tool_use blocks
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        args = func.get("arguments", "{}")
                        # arguments 可能是 JSON 字符串或 dict
                        if isinstance(args, str):
                            try:
                                args = json.loads(args) if args.strip() else {}
                            except json.JSONDecodeError:
                                args = {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "input": args,
                        })

                if content_blocks:
                    anthropic_messages.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })
                else:
                    # 空 assistant 消息兜底
                    anthropic_messages.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": ""}],
                    })
                continue

            # 普通 user / 其他消息 —— 保持 role + content
            anthropic_messages.append({
                "role": role,
                "content": msg.get("content", ""),
            })

        system_prompt = "\n\n".join(system_parts) if system_parts else None

        if not anthropic_messages:
            anthropic_messages = [{"role": "user", "content": "Hello"}]

        return system_prompt, anthropic_messages

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """将 Anthropic SDK Message 响应转换为 Nexus LLMResponse。"""
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                ))

        finish_reason = AnthropicLLM._map_stop_reason(response.stop_reason)

        usage = UsageStats(
            prompt_tokens=response.usage.input_tokens if response.usage else 0,
            completion_tokens=response.usage.output_tokens if response.usage else 0,
            total_tokens=(
                response.usage.input_tokens + response.usage.output_tokens
            ) if response.usage else 0,
        )

        return LLMResponse(
            content="\n".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
            model=response.model,
            finish_reason=finish_reason,
            raw_response=response,
        )

    @staticmethod
    def _map_stop_reason(stop_reason: str | None) -> str:
        """将 Anthropic stop_reason 映射为 Nexus finish_reason。"""
        mapping = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }
        return mapping.get(stop_reason or "", stop_reason or "stop")

    @staticmethod
    def _convert_tools_to_anthropic(
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """将 Nexus OpenAI 格式的 tool schemas 转换为 Anthropic 格式。

        OpenAI:
          {"type": "function", "function": {"name": "x", "description": "...", "parameters": {...}}}

        Anthropic:
          {"name": "x", "description": "...", "input_schema": {...}}
        """
        result: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            func = tool.get("function", tool)
            result.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", func.get("input_schema", {})),
            })
        return result
