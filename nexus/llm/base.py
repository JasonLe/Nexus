"""LLM 抽象层 —— 统一的模型调用接口。

设计思路
--------
面向接口编程（IoC）：Agent Core 只依赖 ``BaseLLM`` 抽象，不依赖任何具体模型
provider。切换模型（OpenAI / Anthropic / vLLM / local）只需替换实例，Core 代码
零修改。

所有方法均为 async，因为 LLM 调用本质是网络 I/O。

扩展约定
--------
- 子类必须实现 ``chat()`` 和 ``stream_chat()``。
- ``chat()`` 返回 ``LLMResponse``，聚合 content / tool_calls / usage。
- ``stream_chat()`` 返回 ``AsyncIterator[LLMChunk]``。
- ``tools`` 参数为 OpenAI 兼容格式的 tool schema list。
- 若 provider 不支持 tool calling，应忽略 ``tools`` 参数并在响应中返回
  ``tool_calls=[]``。

实现提示
--------
- ``chat()`` 可复用 ``stream_chat()``，遍历所有 chunk 聚合为 ``LLMResponse``。
- ``UsageStats`` 应在 ``chat()`` 中由 provider 返回的 token 信息构造。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from nexus.logging import get_logger

logger = get_logger(__name__)


@dataclass
class UsageStats:
    """Token 使用统计。

    Attributes
    ----------
    prompt_tokens : int
        输入（提示）消耗的 token 数。
    completion_tokens : int
        输出（补全）消耗的 token 数。
    total_tokens : int
        prompt_tokens + completion_tokens 的总和。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ToolCall:
    """LLM 返回的工具调用。

    Attributes
    ----------
    id : str
        工具调用的唯一标识，用于后续 tool result 回传匹配。
    name : str
        工具名称，对应 function name。
    arguments : dict[str, Any]
        工具调用参数，key-value 形式的 JSON 对象。
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """LLM 完整响应的数据结构。

    聚合一次完整的 LLM 调用结果，包括文本内容、工具调用、token 统计
    以及原始 provider 响应用于调试。

    Attributes
    ----------
    content : str
        模型生成的文本内容。当触发 tool_calls 时可能为空。
    reasoning_content : str
        思考链内容（适用于支持 thinking 的 provider，如 Anthropic Claude extended thinking、
        MiniMax adaptive thinking 等）。非流式回退路径下从响应中聚合 thinking block；
        流式路径通常为空（已在 LLMChunk.delta_reasoning 中实时返回）。
    tool_calls : list[ToolCall]
        模型请求执行的工具调用列表。
    usage : UsageStats
        本次调用的 token 消耗统计。
    model : str
        实际使用的模型名称。
    finish_reason : str
        结束原因。常见值：
        ``"stop"`` — 自然结束；
        ``"tool_calls"`` — 模型请求调用工具；
        ``"length"`` — 达到 max_tokens 上限；
        ``"content_filter"`` — 被内容安全过滤。
    raw_response : Any
        原始 provider 响应对象，仅用于调试和诊断，不应在业务逻辑中依赖。
    """

    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: UsageStats = field(default_factory=UsageStats)
    model: str = ""
    finish_reason: str = ""
    raw_response: Any = None


@dataclass
class LLMChunk:
    """流式响应的单个数据块。

    在 ``stream_chat()`` 中逐步产出。每个 chunk 包含增量文本和/或增量
    工具调用信息。流结束时 ``finish_reason`` 会被设置。

    Attributes
    ----------
    delta_content : str
        本块的增量文本内容。
    delta_reasoning : str
        本块的思考链增量内容。来源于 OpenAI o1/o3 系列的 reasoning_content
        字段或 Anthropic Claude 的 thinking block。
    delta_tool_calls : list[ToolCall]
        本块新出现的工具调用片段（增量）。
    finish_reason : str | None
        当流结束时，表示结束原因；传输中为 ``None``。
    """

    delta_content: str = ""
    delta_reasoning: str = ""
    delta_tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


class BaseLLM(ABC):
    """LLM 抽象基类 —— 统一的模型调用接口。

    设计思路
    --------
    面向接口编程。Agent Core 只依赖 ``BaseLLM`` 抽象，不依赖任何具体模型
    provider。切换模型只需替换实例，不改 Core 代码。

    所有方法都是 async，因为 LLM 调用本质是网络 I/O。

    扩展约定
    --------
    - 子类必须实现 ``chat()`` 和 ``stream_chat()``。
    - ``chat()`` 返回 ``LLMResponse``（含 content / tool_calls / usage）。
    - ``stream_chat()`` 返回 ``AsyncIterator[LLMChunk]``。
    - ``tools`` 参数为 OpenAI 兼容格式的 tool schema list。
    - 若 provider 不支持 tool calling，应忽略 ``tools`` 参数并在响应中返回
      ``tool_calls=[]``。

    Context Window 防护
    --------------------
    - ``context_window_tokens`` 属性定义模型上下文窗口大小（token 数）。
    - 设为 0 表示不启用自动截断。
    - 子类应在构造函数中设置此值（从配置或模型默认值读取）。
    - ``_maybe_truncate_messages`` 在发送前自动截断超长历史。

    实现提示
    --------
    - ``chat()`` 可以先调用 ``stream_chat()`` 然后聚合所有 chunk 来简化实现。
    - ``UsageStats`` 应在 ``chat()`` 中由 provider 返回的 token 信息构造。
    """

    def __init__(self) -> None:
        self.context_window_tokens: int = 0  # 0 = 不启用自动截断

    def _maybe_truncate_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """若 context_window_tokens > 0 且消息超长，自动截断。

        保留 system 消息和最近若干轮对话，删除最早的历史。
        """
        if self.context_window_tokens <= 0:
            return messages
        # 为输出预留 1/4 空间
        budget = int(self.context_window_tokens * 0.75)
        from nexus.llm.token_counter import truncate_messages_to_fit

        return truncate_messages_to_fit(
            messages, max_tokens=budget, model=self.model
        )

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """发送对话消息并获取完整响应。

        Parameters
        ----------
        messages : list[dict[str, Any]]
            对话消息列表，格式如 ``[{"role": "user", "content": "..."}]``。
        tools : list[dict[str, Any]] | None
            可用工具 schema 列表（OpenAI 兼容格式），``None`` 表示无工具。
        **kwargs : Any
            provider 特定参数，如 ``temperature``、``max_tokens`` 等。

        Returns
        -------
        LLMResponse
            包含文本内容、工具调用和 token 统计的完整响应。
        """
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """流式对话 —— 逐步返回生成内容。

        Parameters
        ----------
        messages : list[dict[str, Any]]
            对话消息列表。
        tools : list[dict[str, Any]] | None
            可用工具 schema 列表（OpenAI 兼容格式）。
        **kwargs : Any
            provider 特定参数。

        Yields
        ------
        LLMChunk
            逐步返回增量内容，每个 chunk 包含 ``delta_content`` 和/或
            ``delta_tool_calls``。
        """
        ...
