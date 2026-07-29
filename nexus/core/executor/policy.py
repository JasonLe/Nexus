"""执行策略抽象模块。

提供 ExecutionPolicy 抽象基类 —— 框架最核心的扩展点之一。
不同的 Policy 实现代表不同的 Agent 思考-行动模式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from nexus.core.executor.actions import Action
from nexus.core.context.context import ExecutionContext
from nexus.logging import get_logger

logger = get_logger(__name__)


class ExecutionPolicy(ABC):
    """执行策略抽象 —— 决定 Agent 的思考-行动模式。

    设计思路
    --------
    这是框架最核心的扩展点之一。将"下一步做什么"的决策权从 Runtime 中
    剥离出来，Runtime 只负责"如何执行"，Policy 负责"决定执行什么"。
    这是 Strategy 模式的体现，让框架支持多种 Agent 模式而不修改 Runtime。

    不同的 Policy 实现代表不同的 Agent 模式：
    - **ReActPolicy**：交替进行 LLM 思考和 Tool 调用，适合需要与环境
      交互的开放式任务。
    - **PlanAndExecutePolicy**：先规划再逐步执行，适合多步骤、有明确
      分解路径的结构化任务。
    - **ReflectionPolicy**：执行后反思并改进，适合需要质量审查和
      迭代优化的任务。

    如何实现新的 Policy
    -------------------
    1. 继承 ``ExecutionPolicy`` 并实现 ``next_action`` 方法。
    2. 在 ``next_action`` 中根据 ``context.state`` 判断当前执行阶段，
       返回对应的 ``Action`` 子类实例。
    3. 如需维护内部状态（如已执行步数、规划结果），在 ``__init__`` 中
       添加属性。注意：context 是一次 run 级别的，state 是跨 Action 的，
       Policy 实例可以在多次 run 间复用（无状态时为佳）。
    4. 将新的 Policy 类注册到 Policy 工厂或配置中，即可在 Runtime 中使用。

    实现约定
    --------
    - ``next_action`` 应是无副作用的纯决策函数（只读 context，不修改 state）。
    - 返回的 Action 由 Runtime 执行并产生副作用（修改 state、调用外部服务等）。
    - 若 Policy 判断需要终止（成功或失败），应返回 ``FinishAction`` 或
      ``ErrorAction``，Runtime 收到后会退出主循环。
    """

    @abstractmethod
    async def next_action(self, context: ExecutionContext) -> Action:
        """根据当前上下文决定下一步 Action。

        Parameters
        ----------
        context : ExecutionContext
            运行时上下文（只读使用）。包含 state、llm、tool_executor
            等所有请求级组件。

        Returns
        -------
        Action
            指示 Runtime 下一步执行什么操作的 Action 实例。

        Notes
        -----
        实现者不应在此方法中修改 ``context.state``。State 的修改由 Runtime
        在成功执行 Action 后完成。这样做的好处是：
        - Policy 变为纯决策函数，可独立测试
        - 所有 state 变更集中在 Runtime 中，便于审计和回滚
        - 避免了 Policy 间通过 state 产生的隐式耦合
        """
        ...
