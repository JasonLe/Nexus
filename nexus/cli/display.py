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

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.status import Status
from rich.table import Table
from rich.text import Text

from nexus.logging import get_logger

logger = get_logger(__name__)

# 模块级共享 Spinner 实例：Thinking box 共用同一对象，确保动画帧时间戳
# 连续，避免每次 update 重建对象导致 spinner 帧抖动。
_THINKING_SPINNER = Spinner("dots", text="Thinking...", style="dim")


def _mask_secret(value: str) -> str:
    """对敏感值应用前3后4掩码（与 server 端 _mask_env_value 规则一致）。

    保留前 3 位与后 4 位，中间以 **** 代替，如 ``sk-****abcd``；
    过短的值直接返回 ``****``。用于 /mcp show 的 env 展示，避免 token 泄露。
    """
    if not value:
        return ""
    if len(value) <= 7:
        return "****"
    return f"{value[:3]}****{value[-4:]}"


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
        ``_response_buffer`` 用于流式回复按段落累积渲染 Markdown：
        chunk 到达时累积到 buffer，遇到段落分隔（连续两个换行）则
        把整段用 Markdown 渲染后 append 输出，既滚动安全又美观。
        """
        self.console = Console()
        self._start_time = time.time()
        self._response_buffer: str = ""
        # Thinking 流式 gutter 的「行首」状态：True 表示下一个非空输出
        # 位于新行行首，需要先补 dim 的 │ 竖线前缀，形成连续可视 gutter
        self._thinking_at_line_start: bool = True

    # ------------------------------------------------------------------
    # 欢迎 / 告别
    # ------------------------------------------------------------------

    def show_thinking_status(self) -> Status:
        """显示"正在思考"状态指示器（底部 spinner）。

        使用 Rich 的 ``console.status()`` 上下文管理器：在屏幕底部
        显示一个 spinner + "Thinking..." 文本，不占用固定行数。
        reasoning 内容在 LLM 调用完成后由调用方用 ``render_thinking``
        一次性渲染到终端（不在 spinner 期间实时显示）。

        设计原因：thinking 实时渲染（Live）会导致 ReAct 多轮循环中
        多个 Thinking Panel 叠加显示。改用 status spinner 完全规避
        Live 重叠问题，且仍能给用户"正在思考"的视觉反馈。

        Returns
        -------
        Status
            Rich Status 上下文管理器实例，调用方需用 ``.stop()`` 关闭。
        """
        status = self.console.status(
            "[dim]🤔 Thinking...[/dim]",
            spinner="dots",
        )
        status.start()
        return status

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
    # 增量流式渲染（事件驱动）
    # ------------------------------------------------------------------
    # 与 render_streaming_response 的区别：
    # - render_streaming_response 接收 AsyncIterator，内部驱动迭代
    # - 以下 4 个方法面向事件回调：start_* 创建 Live 实例返回给调用方，
    #   update_* 在每个 LLM_CHUNK 事件到来时增量更新，调用方在流结束后 stop()
    # ------------------------------------------------------------------

    def start_streaming_thinking(self) -> Live:
        """开启思考链流式渲染，返回 Live 实例供后续更新。

        使用 Panel 包裹 Spinner + Text（Group 组合）：
        - 思考内容为空时显示 Spinner 加载动画
        - 有内容时在 Spinner 下方追加具体思考文本（dim italic）

        标题 🤔 Thinking，灰色边框，与非流式 ``render_thinking`` 样式一致。
        流式开始即显示 loading 状态，让用户在首个思考 chunk 到达前
        就能感知"正在思考"。

        关键设计：``transient=True``，Live stop 时自动擦除整个渲染区域，
        避免 ReAct 多轮循环中多个 Thinking Panel 堆叠。调用方在 stop 后
        应调用 ``render_thinking(accumulated)`` 把最终内容固化为非 Live Panel。

        Returns
        -------
        Live
            已启动的 Rich Live 实例，调用方持有并在每个 chunk 到来时
            调用 ``update_streaming_thinking`` 更新内容，
            流结束后调用 ``live.stop()`` 关闭。
        """
        panel = Panel(
            self._thinking_content(""),
            title="🤔 Thinking",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )
        live = Live(
            panel,
            console=self.console,
            refresh_per_second=10,
            vertical_overflow="visible",
            transient=True,
        )
        live.start()
        return live

    def update_streaming_thinking(self, live: Live, accumulated: str) -> None:
        """更新思考链累积内容。

        内容为空时显示 Spinner 加载动画，有内容时在 Spinner 下方
        追加具体思考文本。每次更新重建 Panel，保持"🤔 Thinking"标题
        和灰色边框常驻，内容区随累积文本增量刷新。

        Parameters
        ----------
        live : Live
            ``start_streaming_thinking`` 返回的 Live 实例。
        accumulated : str
            当前累积的思考链全文（非增量），由调用方维护拼接。
        """
        panel = Panel(
            self._thinking_content(accumulated),
            title="🤔 Thinking",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )
        live.update(panel)

    def _thinking_content(self, accumulated: str) -> RenderableType:
        """构建 Thinking box 的内部内容。

        始终在顶部保留 Spinner（loading 状态指示），
        内容为空时只显示 Spinner，有内容时在 Spinner 下方
        追加具体思考文本（dim italic）。使用 Group 垂直组合。

        Spinner 复用模块级常量 ``_THINKING_SPINNER``，保持帧时间戳连续。
        """
        if not accumulated.strip():
            return _THINKING_SPINNER
        return Group(
            _THINKING_SPINNER,
            Text("", style="dim"),
            Text(accumulated, style="dim italic"),
        )

    def start_streaming_response(self) -> Live:
        """开启回复流式渲染，返回 Live 实例。

        使用 Markdown 渲染增量累积的回复内容，与 render_streaming_response
        的渲染样式一致（亮色 Markdown），但生命周期由调用方管理。

        Returns
        -------
        Live
            已启动的 Rich Live 实例，调用方持有并在每个 chunk 到来时
            调用 ``update_streaming_response`` 更新内容，
            流结束后调用 ``live.stop()`` 关闭。
        """
        live = Live(
            Markdown(""),
            console=self.console,
            refresh_per_second=10,
            vertical_overflow="visible",
            transient=False,
        )
        live.start()
        return live

    def update_streaming_response(self, live: Live, accumulated: str) -> None:
        """更新回复累积内容。

        Parameters
        ----------
        live : Live
            ``start_streaming_response`` 返回的 Live 实例。
        accumulated : str
            当前累积的回复全文（非增量），由调用方维护拼接。
        """
        live.update(Markdown(accumulated))

    def print_response_chunk(self, chunk: str) -> None:
        """以 append 模式打印回复增量文本，按段落渲染 Markdown（滚动安全）。

        采用"段落缓冲"策略平衡美观与滚动安全：
        - chunk 到达时累积到 ``_response_buffer``
        - 遇到段落分隔（``\\n\\n``）时，把整段用 Markdown 渲染后 append 输出
        - 未结束的段落保留在 buffer 中，等下个 chunk 或 ``end_response_stream``

        代码块（``` ... ```）保护：若 buffer 中存在未闭合的 ``` 标记，
        暂不分段输出，避免代码块被拆分导致渲染异常，直到代码块闭合。

        设计原因：
        - Live 重绘模式：滚动时光标定位错乱导致内容错乱（已弃用）
        - 纯 append 原始文本：滚动安全但 Markdown 语法裸露，不美观
        - 段落缓冲：完整段落以 Markdown 渲染（好看），append 输出无光标
          控制序列（滚动安全），未完成段落暂存 buffer 不输出

        Parameters
        ----------
        chunk : str
            本次到达的增量文本（非累积全文）。
        """
        if not chunk:
            return
        self._response_buffer += chunk
        # 代码块保护：若存在未闭合的 ```，暂不分段
        if self._response_buffer.count("```") % 2 == 1:
            return
        # 按段落分隔（双换行）拆分：最后一段可能是未完成的，保留在 buffer
        while "\n\n" in self._response_buffer:
            para, self._response_buffer = self._response_buffer.split("\n\n", 1)
            para = para.strip()
            if para:
                self.console.print(Markdown(para), end="\n\n", soft_wrap=True)
                self.console.file.flush()

    def end_response_stream(self) -> None:
        """流式回复结束：渲染剩余缓冲区内容并打印换行收尾。

        将 buffer 中未结束的段落用 Markdown 渲染输出，
        然后打印换行确保后续内容（Tool/Thinking）从新行开始。
        """
        # 输出 buffer 中剩余的未完成段落
        remaining = self._response_buffer.strip()
        if remaining:
            self.console.print(Markdown(remaining), soft_wrap=True)
            self.console.file.flush()
        self._response_buffer = ""
        self.console.print()

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
        index: int | None = None,
    ) -> None:
        """渲染工具调用（含参数和结果），紧凑单行式 Panel。

        紧凑布局设计（替代旧的 Tree 嵌套）：
        - Panel title 显示序号和工具名：成功 ``🔧 [n] name``，失败 ``❌ [n] name``
        - 内容区第一行为 dim 参数摘要（单行，超长截断）
        - 内容区第二行为结果：✅/❌ 图标 + 结果文本 + 耗时
        - 成功时青色边框，失败时红色边框

        Parameters
        ----------
        tool_name : str
            被调用的工具名称。
        args : dict
            工具参数，用于生成单行参数摘要。
        result : str
            工具执行结果摘要文本（如 "156 lines read"）。
        duration_ms : float
            工具执行耗时（毫秒），可选。
        success : bool
            执行是否成功，决定边框颜色和图标。
        index : int | None
            工具调用序号（从 1 开始），用于 Panel title 显示。
        """
        # 参数摘要：k=v 形式单行拼接，单个值超长截断
        args_summary_parts: list[str] = []
        for k, v in args.items():
            v_str = str(v)
            if len(v_str) > 40:
                v_str = v_str[:37] + "..."
            args_summary_parts.append(f"{k}={v_str}")
        args_summary = ", ".join(args_summary_parts) or "(无参数)"

        icon = "✅" if success else "❌"
        duration_str = f" · {duration_ms:.1f}ms" if duration_ms > 0 else ""

        # 内容区：dim 参数摘要行 + 结果行（Group 垂直组合两行文本）
        content = Group(
            Text(args_summary, style="dim"),
            Text(f"{icon} {result}{duration_str}"),
        )

        # title：🔧 [n] name（失败时 ❌ [n] name），无序号时省略 [n]
        icon_title = "🔧" if success else "❌"
        index_str = f" [{index}]" if index is not None else ""
        title = f"{icon_title}{index_str} {tool_name}"

        self.console.print(Panel(
            content,
            title=title,
            title_align="left",
            border_style="cyan" if success else "red",
            padding=(0, 1),
        ))
        log_fn = logger.info if success else logger.warning
        log_fn("Tool call rendered", extra={"tool_name": tool_name, "success": success})

    # ------------------------------------------------------------------
    # 对话角色标签 & 分隔线
    # ------------------------------------------------------------------

    def render_user_message(self, text: str) -> None:
        """渲染用户消息，绿色边框 Panel + 👤 You 标签。

        用户输入提交后立即调用，将用户消息固定在对话流中，
        避免输入内容随 prompt 滚动消失在终端历史中。

        Parameters
        ----------
        text : str
            用户输入的原始文本。
        """
        self.console.print(Panel(
            Text(text, style="bold white"),
            title="👤 You",
            title_align="left",
            border_style="green",
            padding=(0, 1),
        ))
        logger.debug("User message rendered", extra={"text_length": len(text)})

    def render_assistant_header(self) -> None:
        """渲染 AI 回复起始标签 🤖 Nexus。

        在每轮 AI 输出前显示，作为 AI 内容区域的视觉锚点，
        与用户消息和系统信息形成角色区分。
        """
        self.console.print(Text("🤖 Nexus", style="bold cyan"))
        logger.debug("Assistant header rendered")

    def render_divider(self) -> None:
        """渲染轮次分隔线（dim 灰色横线）。

        在两轮对话之间显示，用终端宽度的横线提供明确的视觉边界，
        防止多轮对话内容混在一起。
        """
        width = self.console.width or 80
        self.console.print(Text("─" * width, style="dim"))
        logger.debug("Divider rendered")

    # ------------------------------------------------------------------
    # 思考过程展示
    # ------------------------------------------------------------------

    def render_thinking(self, text: str) -> None:
        """渲染思考过程，灰色 dim italic Panel + 🤔 Thinking 标签。

        LLM 在调用工具前的 reasoning text（思考链）通过此方法展示，
        与正式回复形成明确的视觉层次区分：思考过程低权重、最终回复高权重。

        Parameters
        ----------
        text : str
            思考过程文本。
        """
        if not text.strip():
            return
        self.console.print(Panel(
            Text(text, style="dim italic"),
            title="🤔 Thinking",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        ))
        logger.debug("Thinking rendered", extra={"text_length": len(text)})

    def print_thinking_start(self) -> None:
        """流式思考开始：打印 🤔 Thinking 标题（静态，无 spinner）。

        纯 append 模式，不使用 Live/Status，不发送任何光标控制序列，
        滚动绝对安全。思考内容通过 ``print_thinking_chunk`` 增量追加，
        每行行首带 dim 的 ``│ `` 竖线前缀，形成连续的可视 gutter 块。

        设计原因：Live（transient=True）在 stop 时发送光标控制序列
        擦除内容，用户滚动终端后光标定位错乱，导致擦除失败、旧内容
        残留 + render_thinking 重复打印 = 多次输出。改用纯 append
        彻底消除光标控制序列。
        """
        # 每轮思考流开始时重置行首状态，确保首行内容也带 gutter 前缀
        self._thinking_at_line_start = True
        self.console.print("[dim]🤔 Thinking[/dim]")

    def print_thinking_chunk(self, chunk: str) -> None:
        """流式打印思考内容（dim italic，行首 │ gutter，append 模式，滚动安全）。

        逐段处理 chunk 中的换行：维护「行首」状态
        （``_thinking_at_line_start``），每个新行的首个非空输出前补
        dim 的 ``│ `` 前缀。chunk 内不含换行时直接追加到当前行，
        跨 chunk 的不完整行由状态位保证前缀只补一次。

        Parameters
        ----------
        chunk : str
            本次到达的增量思考文本。
        """
        if not chunk:
            return
        # 按 \n 拆分逐段处理：split 后除首段外，每段之前都隐含一个换行
        segments = chunk.split("\n")
        for i, segment in enumerate(segments):
            if i > 0:
                # 输出换行本身，进入新行行首状态
                self.console.print("", end="\n", soft_wrap=True)
                self._thinking_at_line_start = True
            if segment:
                if self._thinking_at_line_start:
                    # 行首：先补 gutter 竖线前缀（dim），再输出内容
                    self.console.print("│ ", style="dim", end="", soft_wrap=True)
                    self._thinking_at_line_start = False
                self.console.print(segment, style="dim italic", end="", soft_wrap=True)
        self.console.file.flush()

    def end_thinking_stream(self) -> None:
        """流式思考结束：打印换行收尾，并重置 gutter 行首状态。"""
        self.console.print()
        self._thinking_at_line_start = True

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
        logger.warning("Warning rendered to console", extra={"warning_text": message[:100]})

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

    def render_sessions_table(self, sessions: list[dict]) -> None:
        """渲染历史会话列表为 Rich Table。

        在 ``nexus sessions list`` 与 ``nexus --list-sessions`` 中复用，
        将 SessionManager.list_sessions() 返回的会话列表渲染为
        四列表格：ID / 创建时间 / 消息数 / 摘要。

        Parameters
        ----------
        sessions : list[dict]
            每项应含字段：id / timestamp / message_count / summary
            （log_file 字段不在表格中展示，仅在 delete 操作时显示）
        """
        table = Table(title="Nexus Sessions", show_lines=False, border_style="cyan")
        table.add_column("ID", style="bold cyan", width=10)
        table.add_column("Created", style="dim", width=20)
        table.add_column("Msgs", justify="right", width=6)
        table.add_column("Summary", overflow="fold")

        for s in sessions:
            table.add_row(
                s.get("id", ""),
                s.get("timestamp", ""),
                str(s.get("message_count", 0)),
                s.get("summary") or "(empty)",
            )

        self.console.print(table)
        logger.info("Sessions table rendered", extra={"count": len(sessions)})

    # ------------------------------------------------------------------
    # 工具列表 / 帮助
    # ------------------------------------------------------------------

    def render_tools_table(self, tools: list) -> None:
        """渲染已注册工具列表为 Rich Table。

        在 ``/tools`` 命令中使用，将工具注册表中的工具渲染为三列表格：
        名称（cyan 加粗）/ 描述（截断到 ~50 字符）/ 参数（必填加 ``*``）。

        按工具名前缀 ``mcp__`` 分组：内置工具与 MCP 工具分别渲染为两张表，
        MCP 工具表额外带「Server」列（从 ``mcp__{server}__{tool}`` 解析）。
        无 MCP 工具时保持原单表渲染，行为与之前完全一致。

        Parameters
        ----------
        tools : list
            工具对象列表，每个工具应具有 ``name`` / ``description`` /
            ``schema``（JSON Schema dict）属性。空列表时打印 dim 提示。
        """
        if not tools:
            self.console.print(Text("（暂无已注册的工具）", style="dim"))
            logger.debug("Tools table rendered (empty)")
            return

        # 按 mcp__ 前缀分组：内置工具 / MCP 工具
        builtin_tools = [t for t in tools if not str(getattr(t, "name", "")).startswith("mcp__")]
        mcp_tools = [t for t in tools if str(getattr(t, "name", "")).startswith("mcp__")]

        # 存在 MCP 工具时分组渲染，否则保持原单表标题（向后兼容）
        builtin_title = "内置工具" if mcp_tools else "已注册工具"

        if builtin_tools:
            table = Table(title=builtin_title, show_lines=False, border_style="cyan")
            table.add_column("名称", style="bold cyan")
            table.add_column("描述", overflow="fold")
            table.add_column("参数", style="dim", overflow="fold")

            for tool in builtin_tools:
                desc = str(getattr(tool, "description", "") or "")
                if len(desc) > 50:
                    desc = desc[:47] + "..."
                table.add_row(
                    str(getattr(tool, "name", "unknown")),
                    desc,
                    self._format_tool_params(getattr(tool, "schema", None)),
                )

            self.console.print(table)

        if mcp_tools:
            mcp_table = Table(title="MCP 工具", show_lines=False, border_style="magenta")
            mcp_table.add_column("名称", style="bold magenta")
            mcp_table.add_column("Server", style="cyan")
            mcp_table.add_column("描述", overflow="fold")
            mcp_table.add_column("参数", style="dim", overflow="fold")

            for tool in mcp_tools:
                name = str(getattr(tool, "name", "unknown"))
                desc = str(getattr(tool, "description", "") or "")
                if len(desc) > 50:
                    desc = desc[:47] + "..."
                mcp_table.add_row(
                    name,
                    self._extract_mcp_server(name),
                    desc,
                    self._format_tool_params(getattr(tool, "schema", None)),
                )

            self.console.print(mcp_table)

        logger.info("Tools table rendered", extra={"count": len(tools)})

    @staticmethod
    def _extract_mcp_server(tool_name: str) -> str:
        """从 ``mcp__{server}__{tool}`` 形式的工具名解析所属 server 名。"""
        parts = tool_name.split("__")
        if len(parts) >= 3:
            return parts[1]
        return "-"

    # ------------------------------------------------------------------
    # MCP server 状态 / 工具列表
    # ------------------------------------------------------------------

    def render_mcp_servers(self, status_list: list[dict]) -> None:
        """渲染 MCP server 状态列表为 Rich Table。

        在 ``/mcp`` / ``/mcp list`` 命令中使用，列为：
        名称 / 类型（stdio|http）/ 启用 / 状态（配颜色）/ 工具数。
        状态配色：connected 绿、error 红、disabled 灰、其余黄色。
        空列表时打印 dim 提示并引导 ``/mcp add``。

        Parameters
        ----------
        status_list : list[dict]
            ``MCPManager.get_status()`` 的返回项，每项含
            ``name`` / ``transport`` / ``enabled`` / ``status`` /
            ``error`` / ``tool_count``。
        """
        if not status_list:
            self.console.print(Text(
                "（暂无 MCP server，使用 /mcp add <名称> <命令> [参数...] "
                "或 /mcp add <名称> --url <url> 添加）",
                style="dim",
            ))
            logger.debug("MCP servers table rendered (empty)")
            return

        status_styles = {
            "connected": "green",
            "error": "red",
            "disabled": "dim",
        }

        table = Table(title="MCP Servers", show_lines=False, border_style="cyan")
        table.add_column("名称", style="bold cyan")
        table.add_column("类型", style="dim")
        table.add_column("启用", justify="center")
        table.add_column("状态")
        table.add_column("工具数", justify="right")
        table.add_column("命令", style="dim", overflow="fold")

        for item in status_list:
            status = str(item.get("status", "unknown"))
            style = status_styles.get(status, "yellow")
            status_text = Text(status, style=style)
            error = item.get("error")
            if status == "error" and error:
                error_str = str(error)
                if len(error_str) > 40:
                    error_str = error_str[:37] + "..."
                status_text.append(f" ({error_str})", style="red")
            cmd = item.get("command") or ""
            if cmd:
                args = item.get("args") or []
                cmd = f"{cmd} {' '.join(args)}" if args else cmd
            elif item.get("url"):
                cmd = item.get("url")
            else:
                cmd = "-"
            table.add_row(
                str(item.get("name", "unknown")),
                str(item.get("transport", "stdio")),
                "✓" if item.get("enabled") else "✗",
                status_text,
                str(item.get("tool_count", 0)),
                cmd,
            )

        self.console.print(table)
        logger.info("MCP servers table rendered", extra={"count": len(status_list)})

    def render_mcp_tools(self, server_name: str, tools: list[dict]) -> None:
        """渲染指定 MCP server 的远端工具列表为 Rich Table。

        在 ``/mcp tools <name>`` 命令中使用，列为：注册名（cyan 加粗）/ 描述。
        空列表时打印 dim 提示。

        Parameters
        ----------
        server_name : str
            MCP server 名称，用于表格标题。
        tools : list[dict]
            工具信息列表，每项含 ``name`` / ``description``。
        """
        if not tools:
            self.console.print(Text(
                f"（server '{server_name}' 暂无已注册的工具）", style="dim",
            ))
            logger.debug("MCP tools table rendered (empty)", extra={"server": server_name})
            return

        table = Table(
            title=f"MCP 工具 · {server_name}", show_lines=False, border_style="magenta",
        )
        table.add_column("名称", style="bold magenta")
        table.add_column("描述", overflow="fold")

        for tool in tools:
            desc = str(tool.get("description", "") or "")
            if len(desc) > 60:
                desc = desc[:57] + "..."
            table.add_row(str(tool.get("name", "unknown")), desc)

        self.console.print(table)
        logger.info(
            "MCP tools table rendered",
            extra={"server": server_name, "count": len(tools)},
        )

    def render_mcp_detail(
        self,
        server_name: str,
        cfg: Any,
        status: dict[str, Any],
    ) -> None:
        """渲染单个 MCP server 的完整配置与运行状态（/mcp show <名称>）。

        与列表不同，这里完整展示 command / args / env（掩码）/ url /
        enabled / status / error（不截断），便于排查连接失败原因。

        Parameters
        ----------
        server_name : str
            server 名称。
        cfg : Any
            MCPServerConfig 实例（duck-typed：command/args/env/url/enabled）。
        status : dict[str, Any]
            运行状态（manager.get_status() 项或空 dict）。
        """
        from nexus.cli.config import MCPServerConfig

        if not isinstance(cfg, MCPServerConfig):
            cfg = MCPServerConfig(
                command=getattr(cfg, "command", None),
                args=list(getattr(cfg, "args", None) or []),
                env=dict(getattr(cfg, "env", None) or {}),
                url=getattr(cfg, "url", None),
                enabled=bool(getattr(cfg, "enabled", True)),
            )

        status_text = str(status.get("status", "unknown"))
        style = "green" if status_text == "connected" else (
            "red" if status_text == "error" else (
                "dim" if status_text == "disabled" else "yellow"
            )
        )

        lines = [
            f"名称: {server_name}",
            f"类型: {cfg.transport}",
            f"启用: {'✓' if cfg.enabled else '✗'}",
            f"状态: {status_text}",
        ]
        if cfg.command:
            lines.append(f"命令: {cfg.command}")
        if cfg.args:
            lines.append(f"参数: {' '.join(cfg.args)}")
        if cfg.url:
            lines.append(f"URL: {cfg.url}")
        # env 掩码展示（避免 token 泄露）
        if cfg.env:
            lines.append("环境变量:（掩码展示）")
            for k, v in cfg.env.items():
                lines.append(f"env {k}: {_mask_secret(v)}")
        error = status.get("error")
        if error:
            lines.append("")
            lines.append(f"错误详情: {error}")

        table = Table(
            title=f"MCP 详情 · {server_name}", show_lines=True, border_style="cyan",
        )
        table.add_column("项", style="bold cyan", no_wrap=True)
        table.add_column("值", overflow="fold")
        for line in lines:
            key, _, value = line.partition(": ")
            if value or key == "错误详情":
                table.add_row(key, value)
        self.console.print(table)
        logger.info(
            "MCP detail rendered",
            extra={"server": server_name, "status": status_text},
        )

    @staticmethod
    def _format_tool_params(schema: dict | None) -> str:
        """从 JSON Schema 提取参数名列表，必填参数加 ``*`` 后缀。

        从 ``properties`` 取参数名、``required`` 取必填集合，
        逗号连接后超长截断，供工具表格的「参数」列使用。
        """
        if not isinstance(schema, dict):
            return "-"
        properties = schema.get("properties") or {}
        if not properties:
            return "-"
        required = set(schema.get("required") or [])
        names = [f"{name}*" if name in required else name for name in properties]
        params = ", ".join(names)
        if len(params) > 40:
            params = params[:37] + "..."
        return params

    def render_help_panel(self) -> None:
        """渲染帮助信息为 Rich Panel（内置命令表 + 快捷键说明）。

        在 ``/help`` 命令中使用，全中文内容：
        - 上半部分为内置命令表（命令 / 说明两列）
        - 下半部分为快捷键表（快捷键 / 说明两列）
        外层 Panel 青色边框，title 为「帮助」。
        """
        # 内置命令表：box=None 去掉内部边框，保持 Panel 内紧凑排版
        cmd_table = Table(show_header=True, header_style="bold cyan",
                          box=None, padding=(0, 2))
        cmd_table.add_column("命令", style="bold cyan")
        cmd_table.add_column("说明")
        cmd_table.add_row("/clear", "清空对话上下文")
        cmd_table.add_row("/save", "保存当前会话")
        cmd_table.add_row("/tools", "列出已注册工具")
        cmd_table.add_row("/mcp", "管理 MCP server（list/tools/add/remove/enable/disable/reconnect）")
        cmd_table.add_row("/quit, /exit", "退出 REPL")
        cmd_table.add_row("/help", "显示此帮助")

        # 快捷键表
        key_table = Table(show_header=True, header_style="bold cyan",
                          box=None, padding=(0, 2))
        key_table.add_column("快捷键", style="bold yellow")
        key_table.add_column("说明")
        key_table.add_row("Ctrl+C", "中断当前任务")
        key_table.add_row("Ctrl+D", "退出 REPL")
        key_table.add_row("Esc+Enter", "插入换行（多行输入）")

        self.console.print(Panel(
            Group(cmd_table, Text(""), key_table),
            title="帮助",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        ))
        logger.debug("Help panel rendered")

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
