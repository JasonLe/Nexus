"""执行器模块。

提供 Action 类型族和 ExecutionPolicy 抽象，构成 Agent 框架的调度层。
Policy 决定"做什么"，Runtime 负责"怎么做"，二者通过 Action 解耦。
"""

from nexus.core.executor.actions import (
    Action,
    LLMCallAction,
    ToolCallAction,
    PlanAction,
    ReflectAction,
    FinishAction,
    ErrorAction,
)
from nexus.core.executor.policy import ExecutionPolicy

__all__ = [
    "Action",
    "LLMCallAction",
    "ToolCallAction",
    "PlanAction",
    "ReflectAction",
    "FinishAction",
    "ErrorAction",
    "ExecutionPolicy",
]
