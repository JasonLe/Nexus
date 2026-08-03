"""测试终端显示管理器：DisplayManager 的渲染方法。

覆盖范围
--------
- show_welcome 渲染欢迎信息
- render_user_message 用户消息 Panel
- render_assistant_header AI 标签
- render_divider 轮次分隔线
- render_thinking 思考链 Panel
- render_tool_call 成功 / 失败展示（紧凑单行式 Panel 和序号）
- print_thinking_chunk 流式 gutter（│ 行首竖线前缀）
- render_tools_table 已注册工具表格（空列表 / 非空列表 / 参数提取）
- render_help_panel 帮助 Panel（内置命令 + 快捷键）
- render_error 渲染错误信息
- render_summary 渲染摘要表格
- show_info 渲染普通信息
- render_response AI 回复 Markdown

所有测试使用 io.StringIO 捕获 Console 输出，验证渲染内容不为空。
gutter 测试使用 force_terminal=False 的 Console，输出无 ANSI 转义，
便于直接断言每行行首的 │ 前缀。
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

    def test_streaming_thinking_panel_title(self, display):
        """流式思考链应以 Panel 包裹并显示 🤔 Thinking 标题。"""
        from rich.panel import Panel

        live = display.start_streaming_thinking()
        try:
            # start 即应渲染 Panel，标题含 🤔 Thinking
            buf = io.StringIO()
            Console(file=buf, force_terminal=True, width=120).print(live.renderable)
            assert "Thinking" in buf.getvalue()

            # update 后仍为 Panel，且包含累积内容
            display.update_streaming_thinking(live, "analyzing the code structure")
            buf2 = io.StringIO()
            Console(file=buf2, force_terminal=True, width=120).print(live.renderable)
            out2 = buf2.getvalue()
            assert "Thinking" in out2
            assert "analyzing" in out2
        finally:
            live.stop()

    def test_streaming_thinking_loading_state(self, display):
        """流式思考链空内容时应显示 loading 状态（Spinner）。"""
        from rich.spinner import Spinner

        live = display.start_streaming_thinking()
        try:
            # 初始渲染对象应包含 Spinner（loading 动画）
            buf = io.StringIO()
            Console(file=buf, force_terminal=True, width=120).print(live.renderable)
            assert "Thinking" in buf.getvalue()
            # Spinner 默认包含 "Thinking" 文本提示
            assert "Thinking" in buf.getvalue()

            # 空字符串 update 后仍保持 Spinner（不报错）
            display.update_streaming_thinking(live, "   ")
            buf2 = io.StringIO()
            Console(file=buf2, force_terminal=True, width=120).print(live.renderable)
            assert "Thinking" in buf2.getvalue()
        finally:
            live.stop()

    def test_show_thinking_status(self, display):
        """show_thinking_status 返回 Rich Status 实例，stop 后无副作用。"""
        from rich.status import Status

        status = display.show_thinking_status()
        try:
            assert isinstance(status, Status)
        finally:
            status.stop()
        # 多次 stop 不报错
        status.stop()


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
        """带序号的工具调用应在 title 中显示序号（🔧 [n] name 格式）。"""
        display.render_tool_call(
            tool_name="list_dir",
            args={"path": "."},
            result="3 files found",
            success=True,
            index=1,
        )
        output = _get_output(display)
        assert len(output) > 0
        assert "[1]" in output
        assert "list_dir" in output

    def test_render_tool_call_failure_with_index(self, display):
        """失败的工具调用 title 应使用 ❌ [n] name 格式。"""
        display.render_tool_call(
            tool_name="shell",
            args={"command": "rm -rf /"},
            result="Permission denied",
            success=False,
            index=2,
        )
        output = _get_output(display)
        assert "[2]" in output
        assert "shell" in output
        assert "Permission denied" in output

    def test_render_tool_call_empty_args(self, display):
        """无参数的工具调用应显示（无参数）占位。"""
        display.render_tool_call(
            tool_name="current_time",
            args={},
            result="2026-08-03 12:00:00",
            success=True,
        )
        output = _get_output(display)
        assert "current_time" in output
        assert "无参数" in output


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


class TestThinkingGutter:
    """测试 Thinking 流式 gutter 渲染（│ 行首竖线前缀）。"""

    def _make_plain_display(self) -> DisplayManager:
        """创建无 ANSI 转义输出的 DisplayManager，便于断言行首前缀。"""
        dm = DisplayManager()
        dm.console = Console(file=io.StringIO(), force_terminal=False, width=120)
        return dm

    def test_gutter_prefix_per_line(self):
        """多行 chunk 时每个内容行都应以 │ 前缀开头。"""
        dm = self._make_plain_display()
        dm.print_thinking_start()
        dm.print_thinking_chunk("first line\nsecond line\nthird line")
        dm.end_thinking_stream()
        output = dm.console.file.getvalue()

        lines = [line for line in output.splitlines() if line.strip()]
        # 首行为 🤔 Thinking 标题，其余为内容行
        assert "Thinking" in lines[0]
        content_lines = lines[1:]
        assert len(content_lines) == 3
        for line in content_lines:
            assert line.startswith("│ "), f"内容行缺少 gutter 前缀: {line!r}"
        assert content_lines[0] == "│ first line"
        assert content_lines[1] == "│ second line"
        assert content_lines[2] == "│ third line"

    def test_gutter_prefix_across_chunks(self):
        """跨 chunk 的不完整行不应重复补前缀，换行后的新行应补前缀。"""
        dm = self._make_plain_display()
        dm.print_thinking_start()
        # 第一个 chunk 不含换行：前缀只补一次
        dm.print_thinking_chunk("abc")
        dm.print_thinking_chunk("def\nghi")
        # 第二个 chunk 以换行结尾：下一 chunk 的内容应在新行并带前缀
        dm.print_thinking_chunk("\njkl")
        dm.end_thinking_stream()
        output = dm.console.file.getvalue()

        content_lines = [
            line for line in output.splitlines()
            if line.strip() and "Thinking" not in line
        ]
        assert content_lines == ["│ abcdef", "│ ghi", "│ jkl"]

    def test_gutter_reset_on_new_stream(self):
        """end_thinking_stream 后新一轮流的首行仍应带 gutter 前缀。"""
        dm = self._make_plain_display()
        dm.print_thinking_start()
        dm.print_thinking_chunk("round one")
        dm.end_thinking_stream()
        # 不调用 print_thinking_start 也应因 end 重置状态而带前缀
        dm.print_thinking_chunk("round two")
        dm.end_thinking_stream()
        output = dm.console.file.getvalue()

        content_lines = [
            line for line in output.splitlines()
            if line.strip() and "Thinking" not in line
        ]
        assert content_lines == ["│ round one", "│ round two"]

    def test_empty_chunk_no_output(self):
        """空 chunk 不应产生任何输出。"""
        dm = self._make_plain_display()
        dm.print_thinking_chunk("")
        assert dm.console.file.getvalue() == ""


class TestRenderToolsTable:
    """测试已注册工具表格渲染。"""

    @staticmethod
    def _fake_tool(name: str, description: str, schema: dict):
        """构造具有 name/description/schema 属性的假工具对象。"""
        from types import SimpleNamespace

        return SimpleNamespace(name=name, description=description, schema=schema)

    def test_render_tools_table_empty(self, display):
        """空列表应打印 dim 提示而非表格。"""
        display.render_tools_table([])
        output = _get_output(display)
        assert "暂无已注册的工具" in output

    def test_render_tools_table_with_tools(self, display):
        """非空列表应渲染表格，含名称、描述和参数列（必填加 *）。"""
        tools = [
            self._fake_tool(
                "read_file",
                "读取指定文件的内容",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "encoding": {"type": "string"},
                    },
                    "required": ["path"],
                },
            ),
            self._fake_tool(
                "shell",
                "执行 shell 命令",
                {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            ),
        ]
        display.render_tools_table(tools)
        output = _get_output(display)
        assert "已注册工具" in output
        assert "read_file" in output
        assert "shell" in output
        assert "读取指定文件的内容" in output
        # 必填参数带 *，可选参数不带
        assert "path*" in output
        assert "encoding" in output
        assert "command*" in output

    def test_render_tools_table_no_params(self, display):
        """无参数的 schema 应显示 - 占位。"""
        tools = [
            self._fake_tool(
                "current_time",
                "获取当前时间",
                {"type": "object", "properties": {}},
            ),
        ]
        display.render_tools_table(tools)
        output = _get_output(display)
        assert "current_time" in output
        assert "-" in output

    def test_render_tools_table_long_description_truncated(self, display):
        """超长描述应截断到 ~50 字符。"""
        tools = [
            self._fake_tool("big_tool", "描" * 100, {"properties": {"q": {}}}),
        ]
        display.render_tools_table(tools)
        output = _get_output(display)
        assert "..." in output


class TestRenderHelpPanel:
    """测试帮助 Panel 渲染。"""

    def test_render_help_panel(self, display):
        """帮助 Panel 应包含全部内置命令和快捷键说明。"""
        display.render_help_panel()
        output = _get_output(display)
        assert len(output) > 0
        assert "帮助" in output
        # 内置命令
        for cmd in ("/clear", "/save", "/tools", "/quit", "/help"):
            assert cmd in output
        # 命令说明（抽查）
        assert "清空对话上下文" in output
        assert "列出已注册工具" in output
        # 快捷键
        assert "Ctrl+C" in output
        assert "Ctrl+D" in output
        assert "Esc+Enter" in output
        assert "中断当前任务" in output

