"""测试 ShellTool 工具：跨平台 shell 命令执行。

覆盖范围
--------
- 简单命令执行成功（echo）
- 非零退出码仍返回 success=True（exit 1）
- 超时返回 success=False 并终止进程（sleep + timeout）
- 输出超过阈值时截断并追加提示
- 工作目录参数生效（pwd）
- 缺少必填参数 command 时 ToolExecutor 返回校验失败

注意：当前 venv 未安装 pytest-asyncio，使用 asyncio.run() 包裹异步调用。
"""

import asyncio
import os
import shlex
import sys
import tempfile

from nexus.cli.tools.shell_tool import ShellTool
from nexus.tools.registry import ToolRegistry
from nexus.tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# ShellTool 基本执行测试
# ---------------------------------------------------------------------------


class TestShellToolExecute:
    """测试 ShellTool 的 execute 方法。"""

    def test_simple_command(self):
        """执行简单 echo 命令应成功并返回输出。"""

        async def _run():
            tool = ShellTool(work_dir=os.getcwd())
            return await tool.execute({"command": "echo hello"})

        result = asyncio.run(_run())
        assert result.success is True
        assert "hello" in result.data
        assert "Exit code: 0" in result.data

    def test_nonzero_exit_code(self):
        """非零退出码应返回 success=True，data 包含退出码。"""

        async def _run():
            tool = ShellTool(work_dir=os.getcwd())
            # 使用 exit 1 让命令以非零码退出
            return await tool.execute({"command": "exit 1"})

        result = asyncio.run(_run())
        assert result.success is True
        assert "Exit code: 1" in result.data

    def test_timeout(self):
        """超时应返回 success=False，error 包含 timed out。"""

        async def _run():
            tool = ShellTool(work_dir=os.getcwd())
            # sleep 10 秒但 1 秒后超时
            return await tool.execute({"command": "sleep 10", "timeout": 1})

        result = asyncio.run(_run())
        assert result.success is False
        assert result.error is not None
        assert "timed out" in result.error

    def test_output_truncation(self):
        """输出超过 MAX_OUTPUT 时应截断并追加 [output truncated]。"""

        async def _run():
            tool = ShellTool(work_dir=os.getcwd())
            # 生成 15000 个字符的输出，超过 MAX_OUTPUT=10000
            # 使用 sys.executable 定位 Python 解释器，避免 PATH 中无 python 命令
            py = shlex.quote(sys.executable)
            return await tool.execute({"command": f"{py} -c \"print('x' * 15000)\""})

        result = asyncio.run(_run())
        assert result.success is True
        assert "[output truncated]" in result.data

    def test_work_dir(self):
        """指定 work_dir 时，命令应在对应目录中执行。"""
        # macOS 上 /tmp 是 /private/tmp 的符号链接，使用 realpath 统一比较
        tmp_dir = tempfile.mkdtemp()
        real_dir = os.path.realpath(tmp_dir)

        async def _run():
            tool = ShellTool(work_dir=os.getcwd())
            return await tool.execute({"command": "pwd", "work_dir": tmp_dir})

        result = asyncio.run(_run())
        assert result.success is True
        assert real_dir in result.data

    def test_stderr_captured(self):
        """stderr 输出应被捕获并返回。"""

        async def _run():
            tool = ShellTool(work_dir=os.getcwd())
            # 向 stderr 写入内容
            return await tool.execute({"command": "echo error_msg >&2"})

        result = asyncio.run(_run())
        assert result.success is True
        assert "error_msg" in result.data
        assert "Stderr:" in result.data


# ---------------------------------------------------------------------------
# 参数校验测试（通过 ToolExecutor）
# ---------------------------------------------------------------------------


class TestShellToolValidation:
    """测试 ShellTool 通过 ToolExecutor 的参数校验。"""

    def test_missing_command_param(self):
        """缺少必填参数 command 时应返回 success=False。"""

        async def _run():
            registry = ToolRegistry()
            registry.register(ShellTool(work_dir=os.getcwd()))
            executor = ToolExecutor(registry)
            # 不传 command 参数
            return await executor.execute(
                tool_name="shell",
                tool_call_id="call_shell_001",
                arguments={},
            )

        result = asyncio.run(_run())
        assert result.success is False
        assert result.error is not None
        assert "command" in result.error

    def test_executor_executes_command(self):
        """ToolExecutor 调用 shell 工具应正确执行命令。"""

        async def _run():
            registry = ToolRegistry()
            registry.register(ShellTool(work_dir=os.getcwd()))
            executor = ToolExecutor(registry)
            return await executor.execute(
                tool_name="shell",
                tool_call_id="call_shell_002",
                arguments={"command": "echo from_executor"},
            )

        result = asyncio.run(_run())
        assert result.success is True
        assert "from_executor" in result.data
        assert result.tool_name == "shell"
