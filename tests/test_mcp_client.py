"""测试 MCPClient 的预飞检查与异常消息增强。

覆盖范围
--------
- 预飞检查：stdin 立即崩溃的子进程在握手前被识别，stderr 拼进 RuntimeError。
- 预飞检查：长期运行的子进程（stdin 阻塞等待协议）通过预飞，正常连接。
- 预飞检查：构造参数 / 启动失败（FileNotFoundError）也能产生可读错误。
- ``preflight_timeout=0`` 完全跳过预飞（兼容旧行为 / 测试场景）。
- ``_augment_error`` 行为：McpError 路径 in-place 改 message，普通异常重置 args。
- ``MCPClient._format_preflight_hint`` 行为：> 30 行时插入省略提示；
  检测 ``mcp.server.fastmcp`` 缺失模式并追加锁定 mcp 1.x 的修复建议。

说明：预飞检查启动真实子进程，用 ``sys.executable -c "..."`` 触发 ``exit(1)``
来模拟 import 错误、缺依赖等握手前崩溃场景。子进程被 ``terminate()`` 关闭
不会泄漏（测试结束后自动 reap）。
"""

from __future__ import annotations

import sys

import pytest

from nexus.tools.mcp.client import (
    MCPClient,
    MCP_SDK_AVAILABLE,
    _augment_error,
    _preflight_check,
)


class TestPreflightCheck:
    """测试 ``_preflight_check`` 直接行为。"""

    def test_subprocess_crashes_with_stderr(self):
        """子进程立即崩溃并写 stderr，预飞返回 stderr 内容。"""
        stderr = _preflight_check(
            sys.executable,
            ["-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(1)"],
            env={},
        )
        assert "boom" in stderr

    def test_subprocess_still_running_means_ok(self):
        """子进程在预飞窗口内未退出，视为正常 server（返回空串）。"""
        stderr = _preflight_check(
            sys.executable,
            ["-c", "import time; time.sleep(0.5)"],
            env={},
        )
        assert stderr == ""

    def test_subprocess_not_found(self):
        """不存在的命令返回错误提示。"""
        stderr = _preflight_check(
            "/nonexistent/command/that/should/not/exist",
            [],
            env={},
        )
        assert "无法启动" in stderr or "启动失败" in stderr or "No such" in stderr


@pytest.mark.skipif(not MCP_SDK_AVAILABLE, reason="MCP SDK 未安装")
class TestConnectPreflight:
    """测试 ``MCPClient.connect()`` 的预飞集成。"""

    @pytest.mark.asyncio
    async def test_crashing_subprocess_raises_with_stderr(self):
        """子进程在握手前崩溃时，connect() 抛 RuntimeError 并附 stderr。"""
        client = MCPClient(
            name="test-crash",
            command=sys.executable,
            args=["-c", "import sys; sys.stderr.write('ImportError: bad module\\n'); sys.exit(1)"],
            env={},
            connect_timeout=5.0,
        )
        try:
            with pytest.raises(RuntimeError) as exc_info:
                await client.connect()
            msg = str(exc_info.value)
            assert "ImportError" in msg
            assert "bad module" in msg
            assert "stderr" in msg
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_preflight_disabled_via_zero_timeout(self):
        """``preflight_timeout=0`` 跳过预飞。"""
        client = MCPClient(
            name="test-no-preflight",
            command=sys.executable,
            args=["-c", "import sys; sys.stderr.write('ignored\\n'); sys.exit(1)"],
            env={},
            connect_timeout=2.0,
            preflight_timeout=0,
        )
        try:
            with pytest.raises(Exception):
                await client.connect()
        finally:
            await client.close()


class TestAugmentError:
    """测试 ``_augment_error`` 的两种路径。"""

    def test_mcperror_in_place_message(self):
        """McpError 路径：in-place 修改 .error.message 与 args。"""
        from mcp.shared.exceptions import McpError
        from mcp.types import ErrorData

        exc = McpError(ErrorData(code=1, message="original"))
        result = _augment_error(exc, "traceback line 1\ntraceback line 2")
        assert result is exc
        assert "original" in str(exc)
        assert "[server stderr 尾部]" in str(exc)
        assert "traceback line 1" in str(exc)
        assert "[server stderr 尾部]" in exc.error.message

    def test_plain_exception_resets_args(self):
        """普通异常路径：新建同类型异常并重置 args。"""
        exc = RuntimeError("Connection closed")
        result = _augment_error(exc, "tail line")
        msg = str(result)
        assert "Connection closed" in msg
        assert "[server stderr 尾部]" in msg
        assert "tail line" in msg

    def test_plain_exception_with_multiple_args(self):
        """多 args 异常（args>=2）保留 args[0]，把 args[1] 替换为新消息。"""
        exc = RuntimeError("head", "extra1", "extra2")
        result = _augment_error(exc, "tail")
        msg = str(result)
        assert "head" in msg
        assert "tail" in msg
        assert result.args[0] == "head"
        assert result.args[2:] == ("extra2",)
        assert "[server stderr 尾部]" in result.args[1]
        assert "tail" in result.args[1]


class TestFormatPreflightHint:
    """测试 stderr 截断格式与 mcp<2 兼容提示。"""

    def test_short_stderr_preserved(self):
        """< 30 行的 stderr 完整保留。"""
        text = "\n".join(f"line {i}" for i in range(10))
        result = MCPClient._format_preflight_hint(text)
        assert "line 0" in result
        assert "line 9" in result
        assert "省略" not in result

    def test_long_stderr_truncated(self):
        """>= 30 行的 stderr 截断到尾部 30 行，附省略提示。"""
        text = "\n".join(f"line {i}" for i in range(100))
        result = MCPClient._format_preflight_hint(text)
        assert "line 99" in result
        assert "line 70" in result
        assert "line 0\n" not in result
        assert "省略" in result

    def test_empty_stderr(self):
        """空 stderr 也能处理（不崩）。"""
        result = MCPClient._format_preflight_hint("")
        assert "stderr" in result

    def test_mcp_2x_incompatibility_hint(self):
        """``ModuleNotFoundError: mcp.server.fastmcp`` 模式追加锁定 mcp 1.x 的建议。"""
        stderr = (
            "Traceback (most recent call last):\n"
            "  File \"/path/to/minimax-coding-plan-mcp\", line 6, in <module>\n"
            "    from minimax_mcp.server import main\n"
            "  File \"/path/server.py\", line 17, in <module>\n"
            "    from mcp.server.fastmcp import FastMCP\n"
            "ModuleNotFoundError: No module named 'mcp.server.fastmcp'\n"
        )
        result = MCPClient._format_preflight_hint(stderr)
        # 应同时含原 stderr 与修复建议
        assert "ModuleNotFoundError" in result
        assert "mcp.server.fastmcp" in result
        assert "[可能原因]" in result
        assert "[修复建议]" in result
        assert "mcp<2" in result
        assert "MCPServer" in result

    def test_other_module_not_found_no_hint(self):
        """非 ``mcp.server.fastmcp`` 的 ModuleNotFoundError 不追加 mcp<2 建议。"""
        stderr = "ModuleNotFoundError: No module named 'some_other_lib'\n"
        result = MCPClient._format_preflight_hint(stderr)
        assert "mcp<2" not in result
        assert "[可能原因]" not in result

    def test_mcp_2x_incompatibility_requires_both_markers(self):
        """``mcp.server.fastmcp`` 但不是 ModuleNotFoundError 时不追加建议。"""
        stderr = "some other error mentioning mcp.server.fastmcp but not module not found\n"
        result = MCPClient._format_preflight_hint(stderr)
        assert "[可能原因]" not in result

    def test_npm_invalid_package_name_hint(self):
        """npm EINVALIDPACKAGENAME 模式追加 Unicode 短横线提示。"""
        stderr = (
            "npm error code EINVALIDPACKAGENAME\n"
            "npm error Invalid package name \"@dangahagan/weather‑mcp\" of package "
            "\"@dangahagan/weather‑mcp@latest\": name can only contain URL-friendly characters.\n"
            "npm error A complete log of this run can be found in: /Users/wanghanle/.npm/_logs/2026-08-06T07_48_14_318Z-debug-0.log\n"
        )
        result = MCPClient._format_preflight_hint(stderr)
        # 应含 npm 错误的中文提示
        assert "URL 不友好字符" in result
        assert "npmjs.com" in result
        # 不应触发 mcp<2 提示（elif 互斥）
        assert "mcp<2" not in result
        assert "MCPServer" not in result
