"""CLI 专用内置工具包。

提供 Agent 操作文件系统和执行常见开发任务所需的工具。
不同于 nexus/tools/builtins.py 的通用工具，此处的工具面向终端编程场景。
"""

from nexus.cli.tools.shell_tool import ShellTool

__all__ = ["ShellTool"]
