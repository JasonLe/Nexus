"""Token 计数与 Context Window 防护。

设计思路
--------
1. **估算** —— 在发送 LLM 请求前估算 prompt token 数，避免超出 context window。
2. **截断** —— 超出阈值时自动截断历史消息（保留 system + 最近若干轮）。
3. **降级** —— tiktoken 仅适用于 OpenAI 模型；其他 provider 使用字符近似估算
   （经验值：1 token ≈ 4 字符英文 / ≈ 1.5 字符中文）。

不引入强依赖：tiktoken 为可选依赖，未安装时退化为字符估算。
"""

from __future__ import annotations

import json
from typing import Any

from nexus.logging import get_logger

logger = get_logger(__name__)

# 字符到 token 的近似转换比率（保守估计，略偏高以预留安全边际）
_CHARS_PER_TOKEN_EN = 4.0
_CHARS_PER_TOKEN_CN = 1.5

# tiktoken 缓存（按 model encoding）
_tiktoken_encoders: dict[str, Any] = {}
_tiktoken_available: bool | None = None


def _is_tiktoken_available() -> bool:
    """检查 tiktoken 是否可导入（惰性检测，结果缓存）。"""
    global _tiktoken_available
    if _tiktoken_available is None:
        try:
            import tiktoken  # type: ignore[import-untyped]

            _tiktoken_available = True
        except ImportError:
            _tiktoken_available = False
            logger.debug("tiktoken not installed, falling back to char-based estimation")
    return _tiktoken_available


def _get_tiktoken_encoder(model: str) -> Any | None:
    """获取指定模型的 tiktoken encoder，失败返回 None。"""
    if not _is_tiktoken_available():
        return None
    if model not in _tiktoken_encoders:
        try:
            import tiktoken  # type: ignore[import-untyped]

            _tiktoken_encoders[model] = tiktoken.encoding_for_model(model)
        except Exception:
            # 未知模型退化为 cl100k_base
            try:
                import tiktoken  # type: ignore[import-untyped]

                _tiktoken_encoders[model] = tiktoken.get_encoding("cl100k_base")
            except Exception:
                _tiktoken_encoders[model] = None
    return _tiktoken_encoders[model]


def estimate_message_tokens(
    messages: list[dict[str, Any]],
    model: str = "",
) -> int:
    """估算消息列表的 token 数。

    优先使用 tiktoken（OpenAI 模型精确），否则用字符近似估算。

    Parameters
    ----------
    messages : list[dict]
        OpenAI 格式的消息列表。
    model : str
        模型名称，用于选择 tiktoken encoder。

    Returns
    -------
    int
        估算的 token 数。
    """
    encoder = _get_tiktoken_encoder(model)

    if encoder is not None:
        # 精确计数：每条消息额外 +3 token overhead（role + 结构）
        total = 3  # 基础 overhead
        for msg in messages:
            total += 3  # 每条消息的 overhead
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(encoder.encode(content))
            elif isinstance(content, list):
                # content blocks（如 Anthropic 格式）
                total += len(encoder.encode(json.dumps(content, ensure_ascii=False)))
            # tool_calls 估算
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    total += len(encoder.encode(func.get("name", "")))
                    total += len(encoder.encode(func.get("arguments", "")))
        return total

    # 降级：字符近似估算
    total = 3
    for msg in messages:
        total += 3
        content = msg.get("content", "")
        if isinstance(content, str):
            total += _estimate_text_tokens(content)
        elif isinstance(content, list):
            total += _estimate_text_tokens(json.dumps(content, ensure_ascii=False))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                total += _estimate_text_tokens(func.get("name", ""))
                total += _estimate_text_tokens(func.get("arguments", ""))
    return total


def _estimate_text_tokens(text: str) -> int:
    """字符近似估算 token 数（中英文混合）。"""
    if not text:
        return 0
    # 统计中文字符数（CJK 统一表意文字范围）
    cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - cn_chars
    tokens = cn_chars / _CHARS_PER_TOKEN_CN + other_chars / _CHARS_PER_TOKEN_EN
    return int(tokens) + 1  # 向上取整


def truncate_messages_to_fit(
    messages: list[dict[str, Any]],
    max_tokens: int,
    model: str = "",
    keep_system: bool = True,
    keep_recent: int = 2,
) -> list[dict[str, Any]]:
    """截断消息列表以适应 context window 限制。

    截断策略：
    1. 始终保留 system 消息（若 keep_system=True）。
    2. 保留最近 ``keep_recent`` 条消息。
    3. 从最早的非 system 消息开始删除，直到总 token 数 <= max_tokens。

    Parameters
    ----------
    messages : list[dict]
        原始消息列表。
    max_tokens : int
        context window 上限（token 数）。
    model : str
        模型名称。
    keep_system : bool
        是否始终保留 system 消息。
    keep_recent : int
        保留最近几条消息不截断。

    Returns
    -------
    list[dict]
        截断后的消息列表。
    """
    if max_tokens <= 0:
        return messages

    current_tokens = estimate_message_tokens(messages, model)
    if current_tokens <= max_tokens:
        return messages

    # 分离 system 和非 system 消息（保持相对顺序）
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    # 从最早的非 system 消息开始删除，保留最后 keep_recent 条
    while non_system and keep_recent < len(non_system):
        current_tokens = estimate_message_tokens(system_msgs + non_system, model)
        if current_tokens <= max_tokens:
            break
        # 删除最早的一条（保留最近 keep_recent 条）
        removed = non_system.pop(0)
        logger.debug(
            "Truncated message to fit context window: role=%s, tokens_was=%d",
            removed.get("role", "?"),
            current_tokens,
        )

    result = system_msgs + non_system if keep_system else non_system

    # 若仍然超限（system 本身就很大），再尝试删 system
    if keep_system:
        final_tokens = estimate_message_tokens(result, model)
        if final_tokens > max_tokens and len(system_msgs) > 1:
            # 合并多条 system 为一条精简版
            merged = "\n\n".join(
                m.get("content", "") for m in system_msgs if m.get("content")
            )
            result = [{"role": "system", "content": merged}] + non_system

    return result
