"""运行时上下文模块。

提供 ExecutionContext —— 请求级别的 DI 容器，聚合单次 Agent 运行所需的所有组件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nexus.logging import get_logger

if TYPE_CHECKING:
    from nexus.core.event.event_bus import EventBus
    from nexus.core.state.types import AgentState
    from nexus.tools.executor import ToolExecutor

logger = get_logger(__name__)


@dataclass
class ExecutionContext:
    """运行时上下文 —— 封装单次 Agent 运行所需的所有依赖。

    设计思路
    --------
    ExecutionContext 是一个"请求级别的 DI 容器"。它将单次 Agent run
    所需的所有组件聚合在一起，避免 Runtime/Policy 通过构造函数逐一注入
    大量参数。所有组件通过 context 访问，接口清晰。

    Policy 通过 context 读取 State 做决策，通过 context 获取 tools 信息，
    但 Policy 不修改 state（只读），修改由 Runtime 执行 Action 后完成。

    为何这样设计
    ------------
    1. **减少参数传递**：Runtime 和 Policy 只需接收一个 context 对象，
       而非逐个注入 state、llm、tool_executor、events 等 5+ 个参数。
    2. **统一生命周期管理**：所有请求级组件（如 variables、memory）
       绑定在 context 上，随着一次 run 结束自然释放。
    3. **便于扩展**：新增请求级依赖时只需在 ExecutionContext 中添加字段，
       不影响 Runtime/Policy 的构造函数签名。
    """

    state: "AgentState"
    llm: Any  # BaseLLM 实例（避免循环导入，使用 Any 类型标注）
    tool_executor: "ToolExecutor"
    events: "EventBus"
    max_steps: int = 20
    variables: dict[str, Any] = field(default_factory=dict)
    memory: Any = None  # BaseMemory 实例（预留）
