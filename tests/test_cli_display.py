"""测试终端显示管理器：DisplayManager 的渲染方法。

覆盖范围
--------
- show_welcome 渲染欢迎信息
- render_user_message 用户消息 Panel
- render_assistant_header AI 标签
- render_divider 轮次分隔线
- render_thinking 思考链 Panel
- render_tool_call 成功 / 失败展示（含 Panel 和序号）
- render_error 渲染错误信息
- render_summary 渲染摘要表格
- show_info 渲染普通信息
- render_response AI 回复 Markdown

所有测试使用 io.StringIO 捕获 Console 输出，验证渲染内容不为空。
"""

import io

import pytest

from nexus.cli.display import DisplayManager
from rich.console import Console


@pytest.fixture
def display() -> DisplayManager:
    """创建 DisplayManager 实例，使用 StringIO 捕获输出。"""
    dm = DisplayManager()
    output = io.StringIO()
    dm.console = Console(file=output, force_terminal=True, width=120)
    return dm


def _get_output(display: DisplayManager) -> str:
    """获取 StringIO 中已捕获的全部输出。"""
    return display.console.file.getvalue()


class TestDisplayWelcome:
    """测试欢迎信息渲染。"""

    def test_show_welcome(self, display):
        """渲染欢迎信息不应抛异常，输出内容不为空。"""
        display.show_welcome()
        output = _get_output(display)
        assert len(output) > 0
        assert "Nexus" in output


class TestDisplayUserMessage:
    """测试用户消息渲染。"""

    def test_render_user_message(self, display):
        """渲染用户消息应包含 👤 You 标签和用户输入内容。"""
        display.render_user_message("你好，请列出当前目录的文件")
        output = _get_output(display)
        assert len(output) > 0
        assert "You" in output
        assert "你好" in output


class TestDisplayAssistantHeader:
    """测试 AI 标签渲染。"""

    def test_render_assistant_header(self, display):
        """渲染 AI 标签应包含 🤖 Nexus。"""
        display.render_assistant_header()
        output = _get_output(display)
        assert len(output) > 0
        assert "Nexus" in output


class TestDisplayDivider:
    """测试分隔线渲染。"""

    def test_render_divider(self, display):
        """渲染分隔线应产生非空输出。"""
        display.render_divider()
        output = _get_output(display)
        assert len(output) > 0
        assert "─" in output


class TestDisplayThinking:
    """测试思考链渲染。"""

    def test_render_thinking(self, display):
        """渲染思考链应包含 🤔 Thinking 标签和思考文本。"""
        display.render_thinking("Let me check the directory structure first")
        output = _get_output(display)
        assert len(output) > 0
        assert "Thinking" in output
        assert "directory" in output

    def test_render_thinking_empty(self, display):
        """空字符串不应产生输出。"""
        display.render_thinking("   ")
        output = _get_output(display)
        assert len(output) == 0


class TestDisplayToolCall:
    """测试工具调用渲染。"""

    def test_render_tool_call_success(self, display):
        """渲染成功工具调用应包含工具名和结果。"""
        display.render_tool_call(
            tool_name="read_file",
            args={"path": "app.py"},
            result="156 lines read",
            duration_ms=12.5,
            success=True,
        )
        output = _get_output(display)
        assert len(output) > 0
        assert "read_file" in output
        assert "app.py" in output
        assert "156 lines read" in output

    def test_render_tool_call_failure(self, display):
        """渲染失败工具调用应包含错误信息。"""
        display.render_tool_call(
            tool_name="write_file",
            args={"path": "/root/secret.txt"},
            result="Permission denied",
            duration_ms=3.0,
            success=False,
        )
        output = _get_output(display)
        assert len(output) > 0
        assert "write_file" in output
        assert "Permission denied" in output

    def test_render_tool_call_with_index(self, display):
        """带序号的工具调用应在 title 中显示序号。"""
        display.render_tool_call(
            tool_name="list_dir",
            args={"path": "."},
            result="3 files found",
            success=True,
            index=1,
        )
        output = _get_output(display)
        assert len(output) > 0
        assert "Tool" in output
        assert "list_dir" in output


class TestDisplayError:
    """测试错误渲染。"""

    def test_render_error(self, display):
        """渲染错误信息应包含错误文本。"""
        display.render_error("Connection refused: unable to reach API endpoint")
        output = _get_output(display)
        assert len(output) > 0
        assert "Connection refused" in output


class TestDisplaySummary:
    """测试摘要表格渲染。"""

    def test_render_summary(self, display):
        """渲染摘要表格应包含步数、Token 数和耗时。"""
        display.render_summary(steps=5, total_tokens=2048, duration_ms=3200)
        output = _get_output(display)
        assert len(output) > 0
        assert "5" in output
        assert "2,048" in output
        assert "3200" in output or "ms" in output


class TestDisplayInfo:
    """测试普通信息渲染。"""

    def test_show_info(self, display):
        """渲染普通信息应包含传入的文本。"""
        display.show_info("Loading 3 plugins...")
        output = _get_output(display)
        assert len(output) > 0
        assert "Loading 3 plugins" in output


class TestDisplayResponse:
    """测试 AI 回复渲染。"""

    def test_render_response(self, display):
        """渲染 AI 回复应包含回复内容。"""
        display.render_response("Hello! How can I help you?")
        output = _get_output(display)
        assert len(output) > 0
        assert "Hello" in output

    def test_render_response_empty(self, display):
        """空内容不应产生输出。"""
        display.render_response("")
        output = _get_output(display)
        assert len(output) == 0


class TestShowSpinner:
    """测试 spinner 上下文管理器。"""

    def test_show_spinner_returns_context_manager(self, display):
        """show_spinner 应返回可在 with 语句中使用的上下文管理器。"""
        with display.show_spinner("Working..."):
            pass
        output = _get_output(display)
        assert len(output) >= 0  # spinner 可能输出也可能不输出，只要不抛异常

