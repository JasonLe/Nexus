"""Nexus CLI Shell 工具 —— 提供执行 shell 命令的能力。

设计思路
--------
CLI Agent 在编码场景中常需要执行 shell 命令（运行测试、构建项目、
查看 git 状态等）。ShellTool 提供跨平台的命令执行能力：

- Windows 下使用 ``cmd.exe /c`` 调用命令
- macOS/Linux 下使用 ``/bin/bash -c`` 调用命令

安全设计：
--------
- **超时控制**：默认 30 秒超时，防止长时间运行的命令阻塞 Agent 循环
- **输出截断**：超过 10000 字符的输出会被截断，防止撑爆 LLM 上下文窗口
- **非零退出码视为成功**：命令执行了即 success=True，退出码仅作为信息返回，
  让 LLM 自行判断是否需要重试或修正命令

设计决策（非零退出码 success=True）：
很多有意义的命令会返回非零退出码（如 ``grep`` 未匹配、``pytest`` 测试失败、
``eslint`` 检出问题），这些情况下 Agent 仍需要读取 stdout/stderr 来理解发生了什么。
若视为失败，LLM 只能拿到 error 字段，丢失了 stdout 中的诊断信息。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

from nexus.logging import get_logger
from nexus.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)


class ShellTool(BaseTool):
    """Shell 命令执行工具。

    跨平台执行 shell 命令，自动适配 Windows（cmd.exe）和 macOS/Linux（bash）。

    安全设计：
    --------
    - **超时控制**：默认 30 秒，超时后强制 kill 子进程
    - **输出截断**：stdout/stderr 超过 10000 字符时截断
    - **工作目录隔离**：可指定 work_dir 限制命令执行的工作目录

    使用示例
    --------

    >>> tool = ShellTool(work_dir="/project")
    >>> result = await tool.execute({"command": "pytest tests/"})
    >>> result = await tool.execute({"command": "ls -la", "timeout": 10})
    """

    # 输出截断阈值（字符数），超过此长度的输出会被截断
    MAX_OUTPUT = 10000

    # 默认超时时间（秒）
    DEFAULT_TIMEOUT = 30

    def __init__(self, work_dir: str = ".") -> None:
        """初始化 ShellTool。

        Parameters
        ----------
        work_dir : str
            默认工作目录，命令不指定 work_dir 时使用此目录。
        """
        self.work_dir = os.path.abspath(work_dir)

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return "执行 shell 命令，自动适配 Windows/macOS/Linux。返回退出码、stdout 和 stderr。"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令。",
                },
                "timeout": {
                    "type": "integer",
                    "description": "命令超时秒数，默认 30 秒。",
                    "default": 30,
                },
                "work_dir": {
                    "type": "string",
                    "description": "命令执行的工作目录，不指定则使用默认工作目录。",
                },
            },
            "required": ["command"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """执行 shell 命令并返回结果。

        流程：参数提取 → 构造平台命令 → 子进程执行 → 超时处理 → 输出截断。
        """
        t_start = time.time()
        try:
            command: str = args["command"]
            timeout: int = args.get("timeout", self.DEFAULT_TIMEOUT)
            work_dir: str = args.get("work_dir", self.work_dir)

            # 操作系统检测：Windows 用 cmd.exe，其他用 bash
            if sys.platform == "win32":
                cmd_list = ["cmd.exe", "/c", command]
            else:
                cmd_list = ["/bin/bash", "-c", command]

            # 创建子进程执行命令
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )

            # 等待命令完成，带超时控制
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                # 超时后强制终止子进程，避免僵尸进程
                proc.kill()
                await proc.wait()
                duration_ms = (time.time() - t_start) * 1000
                logger.warning(
                    "Shell command timed out",
                    extra={
                        "tool_name": self.name,
                        "command": command,
                        "timeout": timeout,
                    },
                )
                return ToolResult.fail(
                    error=f"Command timed out after {timeout}s",
                    tool_name=self.name,
                    duration_ms=duration_ms,
                )

            # 解码输出（bytes → str），用 replace 防止解码失败
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            # 输出截断：超过阈值时截断并追加提示
            if len(stdout) > self.MAX_OUTPUT:
                stdout = stdout[: self.MAX_OUTPUT] + "\n... [output truncated]"
            if len(stderr) > self.MAX_OUTPUT:
                stderr = stderr[: self.MAX_OUTPUT] + "\n... [output truncated]"

            duration_ms = (time.time() - t_start) * 1000

            # 构造结果文本
            result_text = (
                f"Exit code: {proc.returncode}\n"
                f"Duration: {duration_ms:.0f}ms\n"
                f"Stdout:\n{stdout}"
                f"Stderr:\n{stderr}"
            )

            logger.info(
                "Shell command executed",
                extra={
                    "tool_name": self.name,
                    "command": command,
                    "exit_code": proc.returncode,
                    "duration_ms": duration_ms,
                },
            )

            # 注意：非零退出码时 success 仍为 True（命令执行了，只是退出码非零）
            return ToolResult.ok(
                data=result_text,
                tool_name=self.name,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.time() - t_start) * 1000
            logger.error(
                "Shell command execution failed",
                extra={"tool_name": self.name, "error": str(e)},
                exc_info=True,
            )
            return ToolResult.fail(
                error=str(e),
                tool_name=self.name,
                duration_ms=duration_ms,
            )
