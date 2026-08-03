"""Nexus Tool 系统 —— 插件化工具框架的核心抽象。

导出
----
- ``BaseTool`` / ``ToolResult`` / ``ToolError`` : 工具基础抽象
- ``ToolRegistry`` : 工具注册中心
- ``ToolExecutor`` : 工具执行器（校验 → 执行 → 记录）
- ``tool`` : @tool 装饰器，快速定义工具元数据
"""

from nexus.tools.base import (
    BaseTool,
    ToolError,
    ToolNotFoundError,
    ToolResult,
    ToolValidationError,
)
from nexus.tools.decorators import tool
from nexus.tools.executor import ToolExecutor
from nexus.tools.file_tools import (
    ListDirTool,
    ReadFileTool,
    SearchContentTool,
    WriteFileTool,
)
from nexus.tools.registry import ToolRegistry
from nexus.tools.shell_tool import ShellTool

__all__ = [
    "BaseTool",
    "tool",
    "ToolError",
    "ToolExecutor",
    "ListDirTool",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolValidationError",
    "ReadFileTool",
    "SearchContentTool",
    "ShellTool",
    "WriteFileTool",
]
