"""Nexus CLI 终端显示管理器 —— 基于 Rich 的流式输出和美化渲染。

设计思路
--------
DisplayManager 封装所有终端输出逻辑，让 main.py 和 repl.py 不需要
关心 Markdown 渲染、Panel 排版、Spinner 动画等细节。

核心能力：
1. 流式 Markdown 渲染（使用 Rich Live 组件，增量更新不闪烁）
2. 工具调用展示（Tree 结构 + spinner）
3. 错误/警告展示（彩色 Panel）
4. 执行统计摘要

设计原因：将显示逻辑与业务逻辑分离，未来可替换为其他渲染后端
（如 Web UI 通过 WebSocket 推送）而不修改 CLI 核心流程。
"""

from __future__ import annotations

import time
from typing import AsyncIterator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from nexus.logging import get_logger

logger = get_logger(__name__)


class DisplayManager:
    """终端显示管理器 —— 封装 Rich 组件的流式渲染。

    职责
    ----
    作为 CLI 的"视图层"，所有终端输出通过此管理器的统一 API 渲染。
    CLI 命令处理逻辑只关心"要展示什么"，而不关心"如何渲染"。

    组件选择说明
    ------------
    - **Live**：用于流式 Markdown，每收到 chunk 增量更新，refresh_per_second=10
      平衡渲染流畅度和 CPU 占用
    - **Tree**：用于工具调用链展示，天然树形结构比嵌套 Panel 更直观
    - **Panel**：用于错误/警告，彩色边框在终端中易识别
    - **Table**：用于摘要统计，列对齐清晰可读
    - **Spinner**：用于指示等待状态，给用户即时反馈

    使用示例
    --------

    >>> dm = DisplayManager()
    >>> dm.show_welcome()
    >>> dm.render_tool_call("read_file", {"path": "app.py"}, "156 lines read")
    >>> summary = dm.render_summary(steps=3, total_tokens=1024, duration_ms=3200)
    """

    def __init__(self) -> None:
        """初始化显示管理器。

        创建 Rich Console 实例并记录启动时间，用于后续统计总耗时。
        """
        self.console = Console()
        self._start_time = time.time()

    # ------------------------------------------------------------------
    # 欢迎 / 告别
    # ------------------------------------------------------------------

    def show_welcome(self) -> None:
        """显示欢迎信息。

        在 REPL 或 CLI 启动时调用，展示 Nexus 品牌标识和版本信息。
        使用 Panel 包裹标题文字，蓝色边框突出品牌感。
        """
        title = Text("Nexus CLI Agent", style="bold cyan")
        subtitle = Text("AI-powered development assistant  ·  Type /help for commands",
                        style="dim")
        self.console.print(Panel(subtitle, title=title, border_style="blue"))
        logger.info("Welcome banner displayed")

    def show_goodbye(self, steps: int, duration: float) -> None:
        """显示告别信息和统计。

        在 session 或 REPL 退出时调用，展示本次会话的执行摘要。

        Parameters
        ----------
        steps : int
            本次会话执行的总步数。
        duration : float
            会话总耗时（秒）。
        """
        text = Text()
        text.append("Session ended.  ", style="dim")
        text.append(f"{steps} steps", style="bold")
        text.append(f" in {duration:.1f}s.", style="dim")
        self.console.print(Panel(text, border_style="cyan"))
        logger.info("Goodbye displayed", extra={"steps": steps})

    # ------------------------------------------------------------------
    # 流式 Markdown 渲染
    # ------------------------------------------------------------------

    async def render_streaming_response(self, content_stream: AsyncIterator[str]) -> str:
        """流式渲染 LLM 响应。

        使用 ``rich.live.Live`` 包裹 ``Markdown`` 实现逐字刷新。
        每次收到新的 chunk，追加到累积内容后调用 ``live.update()`` 更新显示。

        技术决策：
        - ``refresh_per_second=10`` 而非更高频率，因为人类阅读速度有限，
          更高刷新率只会无谓消耗 CPU
        - 使用 ``vertical_overflow="visible"`` 避免长输出被截断
        - ``transient=False`` 确保 Markdown 在流式结束后保留在终端历史中

        Parameters
        ----------
        content_stream : AsyncIterator[str]
            字符串流（LLMChunk.delta_content 的序列），
            每次迭代产出一个增量文本片段。

        Returns
        -------
        str
            完整的累积内容字符串，供后续处理（如保存到日志）。
        """
        accumulated = ""

        # Live 组件的 auto_refresh 由 Rich 内部线程驱动，不阻塞主协程
        with Live(
            Markdown(""),
            console=self.console,
            refresh_per_second=10,  # 每秒刷新 10 次，平衡流畅度与资源消耗
            vertical_overflow="visible",
            transient=False,  # 渲染完成后保留在终端历史中
        ) as live:
            async for chunk in content_stream:
                accumulated += chunk
                # Markdown 对象按需创建，Rich 内部 diff 渲染只更新变化部分
                live.update(Markdown(accumulated))

        logger.debug("Streaming response rendered", extra={"content_length": len(accumulated)})
        return accumulated

    # ------------------------------------------------------------------
    # 工具调用展示
    # ------------------------------------------------------------------

    def render_tool_call(
        self,
        tool_name: str,
        args: dict,
        result: str,
        duration_ms: float = 0,
        success: bool = True,
    ) -> None:
        """渲染工具调用（含参数和结果）。

        使用 Rich Tree 组件呈现工具调用链：
        - 根节点用 🔧 前缀 + 工具名 + 关键参数摘要
        - 子节点用 ✅（成功）或 ❌（失败）+ 结果摘要

        命名决策：
        - 选择 Tree 而非嵌套 Panel，因为复杂工具调用链天然适合树形结构
        - 使用 emoji 而非 ANSI 颜色，在不同终端主题下辨识度一致

        Parameters
        ----------
        tool_name : str
            被调用的工具名称。
        args : dict
            工具参数，用于在 Tree 节点中展示关键信息。
        result : str
            工具执行结果摘要文本（如 "156 lines read"）。
        duration_ms : float
            工具执行耗时（毫秒），可选。
        success : bool
            执行是否成功，决定显示 ✅ 还是 ❌。
        """
        # 构建参数摘要：只展示值，省略过长的参数
        args_summary_parts: list[str] = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 40:
                v_str = v_str[:37] + "..."
            args_summary_parts.append(f"{k}={v_str}")
        args_summary = ", ".join(args_summary_parts)

        # 根节点：工具调用标识
        tree = Tree(f"🔧 {tool_name}({args_summary})")

        # 子节点：执行结果
        icon = "✅" if success else "❌"
        duration_str = f" ({duration_ms:.1f}ms)" if duration_ms > 0 else ""
        tree.add(f"{icon} {result}{duration_str}")

        self.console.print(tree)
        log_fn = logger.info if success else logger.warning
        log_fn("Tool call rendered", extra={"tool_name": tool_name, "success": success})

    # ------------------------------------------------------------------
    # 思考过程展示
    # ------------------------------------------------------------------

    def render_thinking(self, text: str) -> None:
        """渲染思考过程文本。

        用 dim 样式区分于正式回复，使 LLM 的"内心独白"在视觉上与
        最终输出形成层次差异。通常用于展示 reasoning tokens（如 o1 系列）。

        Parameters
        ----------
        text : str
            思考过程文本。
        """
        thinking = Text(text, style="dim italic")
        self.console.print(thinking)
        logger.debug("Thinking text rendered", extra={"text_length": len(text)})

    # ------------------------------------------------------------------
    # 错误 / 警告展示
    # ------------------------------------------------------------------

    def render_error(self, message: str) -> None:
        """渲染错误信息，使用红色 Panel 突出显示。

        红色边框 + 错误内容，确保用户在大量终端输出中能快速定位问题。

        Parameters
        ----------
        message : str
            错误描述文本。
        """
        error_text = Text(message, style="red")
        self.console.print(Panel(error_text, border_style="red", title="Error"))
        logger.error("Error rendered to console", extra={"error_text": message[:100]})

    def render_warning(self, message: str) -> None:
        """渲染警告信息，使用黄色 Panel。

        与 render_error 区分：警告不阻断操作，仅是提醒。

        Parameters
        ----------
        message : str
            警告描述文本。
        """
        warning_text = Text(message, style="yellow")
        self.console.print(Panel(warning_text, border_style="yellow", title="Warning"))
        logger.warning("Warning rendered to console", extra={"message": message[:100]})

    # ------------------------------------------------------------------
    # 执行摘要统计
    # ------------------------------------------------------------------

    def render_summary(self, steps: int, total_tokens: int, duration_ms: float) -> None:
        """渲染执行摘要统计表格。

        使用 Rich Table 组件，两列布局（指标名 | 值），
        在每次 ``agent.run()`` 结束后展示关键统计。

        Parameters
        ----------
        steps : int
            执行的总步数。
        total_tokens : int
            消耗的 Token 总数（含 prompt + completion）。
        duration_ms : float
            执行总耗时（毫秒）。
        """
        table = Table(title="Execution Summary", show_header=False, border_style="cyan")
        # 紧凑布局：两列无表头
        table.add_column("Metric", style="dim", width=16)
        table.add_column("Value", style="bold")

        table.add_row("Steps", str(steps))
        table.add_row("Tokens", f"{total_tokens:,}")
        table.add_row("Duration", f"{duration_ms:.0f} ms")
        table.add_row("Elapsed", f"{self._elapsed():.1f} s")

        self.console.print(table)
        logger.info(
            "Summary rendered",
            extra={"steps": steps, "total_tokens": total_tokens},
        )

    # ------------------------------------------------------------------
    # 信息输出
    # ------------------------------------------------------------------

    def show_info(self, text: str) -> None:
        """显示普通信息，dim 样式。

        适用于中间状态通知（如"正在连接..."、"Thinking..."），
        降低视觉权重，不干扰主要输出。
        """
        self.console.print(Text(text, style="dim"))
        logger.debug("Info displayed", extra={"text": text[:100]})

    def render_response(self, content: str) -> None:
        """渲染 AI 回复文本，亮色样式。

        与 show_info 的区别：show_info 用 dim 灰色显示状态信息，
        render_response 用默认亮色显示 AI 的实际回复内容。
        """
        if content:
            self.console.print(Markdown(content))
            logger.debug("Response rendered", extra={"content_length": len(content)})

    def render_streaming_content(self, content: str) -> None:
        """渲染普通文本内容（非流式）。

        用于一次性展示完整文本（如最终回复），
        区别于 render_streaming_response 的逐字流式渲染。

        Parameters
        ----------
        content : str
            要渲染的文本内容。
        """
        if content:
            self.console.print(Markdown(content))
            logger.debug("Content rendered", extra={"content_length": len(content)})

    def show_spinner(self, message: str) -> Spinner:
        """显示旋转等待动画。

        返回 Spinner 对象，调用方需要配合 ``with`` 语句使用以自动启停：

        >>> with dm.show_spinner("Searching..."):
        ...     result = await agent.search(query)

        返回的是 Rich Spinner 上下文管理器，由调用方管理生命周期。

        Parameters
        ----------
        message : str
            Spinner 旁边的说明文字。

        Returns
        -------
        Spinner
            Rich Spinner 上下文管理器。
        """
        return self.console.status(Spinner("dots", text=Text(message, style="cyan")))

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _elapsed(self) -> float:
        """计算从 DisplayManager 创建到当前的耗时（秒）。"""
        return time.time() - self._start_time
