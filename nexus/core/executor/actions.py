"""Action 类型族模块。

定义 Policy 返回的操作指令类型。Policy 不直接调用 LLM 或 Tool，
而是返回描述"下一步做什么"的 Action 对象，由 Runtime 解释执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Action:
    """Action 基类 —— Policy 返回的操作指令。

    设计思路
    --------
    Action-based 调度模型。Policy 不直接调用 LLM 或 Tool，
    而是返回描述"下一步做什么"的 Action 对象。Runtime 根据 Action 类型
    执行具体操作。

    这个间接层的价值：
    1. Policy 可以是纯函数，方便单元测试（不依赖 LLM/Tool 实例）
    2. Runtime 可以统一记录日志、派发事件、错误处理
    3. 未来可支持 Action 序列化/重放，实现执行轨迹的完整复现
    """

    pass


@dataclass
class LLMCallAction(Action):
    """调用 LLM 生成回复。

    使用场景：Policy 判断需要 LLM 进行推理/规划/总结时返回此 Action。
    Runtime 收到后调用 LLM 并将响应写回 state。
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] | None = None


@dataclass
class ToolCallAction(Action):
    """调用工具执行任务。

    使用场景：LLM 返回 tool_calls 后，Policy 解析为 ToolCallAction，
    Runtime 调用 ToolExecutor 执行并将结果写回 conversation history。
    """

    tool_name: str = ""
    tool_call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanAction(Action):
    """进入规划阶段 —— 生成执行计划。

    使用场景：PlanAndExecute 模式中，Agent 首先需要将复杂任务拆解为
    可执行的步骤序列。Policy 返回此 Action 后，Runtime 驱动 LLM 生成计划。
    """

    task: str = ""


@dataclass
class ReflectAction(Action):
    """进入反思阶段 —— 审视已执行步骤。

    使用场景：Reflection 模式中，Agent 在完成一轮执行后需要回顾已完成的
    步骤，评估结果质量并决定是否需要调整策略。Policy 返回此 Action 后，
    Runtime 驱动 LLM 进行反思分析。
    """

    pass


@dataclass
class FinishAction(Action):
    """结束执行。

    使用场景：Policy 判断任务已完成（达成目标）或达到终止条件时返回此 Action。
    Runtime 收到后退出主循环并返回最终结果。
    """

    result: Any = None
    message: str = ""


@dataclass
class ErrorAction(Action):
    """错误终止。

    使用场景：执行过程中发生不可恢复的错误时，Policy（或 Runtime 兜底逻辑）
    返回此 Action，Runtime 退出主循环并将错误信息传递给调用方。
    """

    error: str = ""
    exception: Exception | None = None
