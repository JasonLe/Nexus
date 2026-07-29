"""MiniMax Anthropic Provider —— 继承 AnthropicLLM，切换端点和模型。

设计思路
--------
MiniMax 提供 Anthropic Messages API 兼容端点:
  https://api.minimaxi.com/anthropic

由于 API 格式完全兼容，只需继承 AnthropicLLM 并覆写:
- 默认 model: MiniMax-Text-01
- 默认 base_url: https://api.minimaxi.com/anthropic
- 环境变量: MINIMAX_API_KEY

所有 Anthropic ↔ Nexus 格式映射、tool calling 转换、流式处理逻辑
均由父类 AnthropicLLM 提供，无需在本类中重复实现。

支持模型: MiniMax-Text-01, abab6.5s-chat 等
"""

from __future__ import annotations

from typing import Any

from nexus.llm.providers.anthropic import AnthropicLLM


class MiniMaxAnthropicLLM(AnthropicLLM):
    """MiniMax LLM Provider —— 薄包装 AnthropicLLM，切换端点和默认模型。

    使用 Anthropic Python SDK，指向 MiniMax 的 Anthropic 兼容端点。
    所有核心逻辑继承自父类 AnthropicLLM。

    Parameters
    ----------
    api_key : str | None
        API 密钥。默认从环境变量 ``MINIMAX_API_KEY`` 读取。
    base_url : str
        API 端点，默认 ``https://api.minimaxi.com/anthropic``。
    model : str
        模型名称，默认 ``MiniMax-Text-01``。
    **kwargs : Any
        透传给 ``AsyncAnthropic`` 构造函数的额外参数。

    使用示例
    --------

    >>> from nexus.llm.providers.minimax import MiniMaxAnthropicLLM
    >>>
    >>> llm = MiniMaxAnthropicLLM(model="MiniMax-Text-01")
    >>> response = await llm.chat(
    ...     messages=[{"role": "user", "content": "Hello!"}],
    ... )
    """

    _env_key = "MINIMAX_API_KEY"
    _default_model = "MiniMax-Text-01"
    _default_base_url = "https://api.minimaxi.com/anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.minimaxi.com/anthropic",
        model: str = "MiniMax-Text-01",
        **kwargs: Any,
    ) -> None:
        # 直接调用父类构造函数——所有 SDK 调用逻辑在 AnthropicLLM 中
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            **kwargs,
        )
