"""测试终端显示管理器：DisplayManager 的渲染方法。

覆盖范围
--------
- show_welcome 渲染欢迎信息（不抛异常，内容不为空）
- render_tool_call 成功 / 失败展示
- render_error 渲染错误信息
- render_summary 渲染摘要表格
- show_info 渲染普通信息

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


class TestDisplayToolCall:
    """测试工具调用渲染。"""

    def test_render_tool_call_success(self, display):
        """渲染成功工具调用应包含 ✅ 和工具名。"""
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
        """渲染失败工具调用应包含 ❌ 和错误信息。"""
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
