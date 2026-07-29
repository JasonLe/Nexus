"""运行时上下文模块。

提供 ExecutionContext —— 请求级别的 DI 容器，封装单次 Agent 运行所需的所有依赖。
"""

from nexus.core.context.context import ExecutionContext

__all__ = ["ExecutionContext"]
