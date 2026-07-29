"""Nexus CLI 交互式 REPL —— 基于 prompt_toolkit + Rich 的终端对话界面。

设计思路
--------
Repl 类封装了交互式对话的全部流程：
1. 使用 prompt_toolkit 的 PromptSession 获取用户输入
2. 将用户输入作为 task 提交给 Agent.run()
3. 通过 EventBus 订阅事件，实时流式展示 LLM 输出和工具调用
4. 支持内置命令（/clear /save /tools /quit /help）

与 opencode/pi 的差异：
- opencode 使用 Go + Bubble Tea TUI，Nexus 使用 Python + prompt_toolkit
- 更轻量，不需要完整 TUI 框架，保持快速启动

设计原因：REPL 是 CLI Agent 最直观的使用方式，
用户可以增量式地与 Agent 交互，逐步完善需求。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from nexus.cli.config import NexusConfig
from nexus.cli.display import DisplayManager
from nexus.cli.session import SessionManager
from nexus.core.agent.agent import Agent
from nexus.core.event.event_types import EventType
from nexus.core.event.types import Event
from nexus.core.state.types import AgentState
from nexus.logging import get_logger

logger = get_logger(__name__)


class Repl:
    """Nexus 交互式 REPL。

    提供类 ChatGPT 的终端对话体验：
    - 多轮对话保持上下文
    - 流式 LLM 输出实时显示
    - 内置命令扩展
    - 历史记录持久化

    Attributes:
        agent: Nexus Agent 实例
        display: 终端显示管理器
        config: CLI 配置
        session_mgr: 会话管理器
    """

    def __init__(
        self,
        agent: Agent,
        display: DisplayManager,
        config: NexusConfig,
        session_mgr: SessionManager | None = None,
    ) -> None:
        """初始化 REPL。

        Parameters
        ----------
        agent : Agent
            Agent 实例，负责执行用户任务。
        display : DisplayManager
            终端显示管理器。
        config : NexusConfig
            CLI 配置。
        session_mgr : SessionManager | None
            会话管理器，为 None 时自动创建默认实例。
        """
        self.agent = agent
        self.display = display
        self.config = config
        self.session_mgr = session_mgr or SessionManager()
        self._running = False

        # 跨轮对话历史：存储所有 run 的 messages，实现多轮对话上下文保持
        # 每条元素为 {"role": str, "content": str} 格式
        self._conversation_history: list[dict[str, Any]] = []

        # prompt_toolkit session：使用 FileHistory 实现跨进程历史记录持久化
        history_file = Path.home() / ".nexus" / "repl_history"
        history_file.parent.mkdir(parents=True, exist_ok=True)

        self._session = PromptSession(
            history=FileHistory(str(history_file)),
            style=self._get_style(),
            key_bindings=self._get_key_bindings(),
            completer=self._get_completer(),
        )

        logger.info(
            "Repl initialized",
            extra={
                "agent_name": agent.name,
                "history_file": str(history_file),
            },
        )

    # ------------------------------------------------------------------
    # prompt_toolkit 配置
    # ------------------------------------------------------------------

    def _get_style(self) -> Style:
        """定义 prompt_toolkit 的样式。

        使用简洁的配色方案：
        - 提示符（> ）用绿色加粗，醒目但不刺眼
        - 内置命令用灰色斜体，降低视觉权重以区分于普通输入
        """
        return Style.from_dict({
            "prompt": "#00aa00 bold",
            "cmd": "#888888 italic",
        })

    def _get_key_bindings(self) -> KeyBindings:
        """定义快捷键。

        - Ctrl+D: 退出 REPL
        - Esc + Enter: 插入换行符（用于多行输入场景）
        prompt_toolkit 自带 Ctrl+C 中断、上下箭头历史等默认快捷键。
        """
        kb = KeyBindings()

        @kb.add("c-d")
        def _(event: Any) -> None:
            """Ctrl+D 退出 REPL。"""
            event.app.exit(result=None)

        @kb.add("escape", "enter")
        def _(event: Any) -> None:
            """Esc+Enter 在当前光标位置插入换行符。"""
            event.current_buffer.insert_text("\n")

        return kb

    def _get_completer(self) -> WordCompleter:
        """自动补全：内置命令。

        输入 / 后 Tab 即可补全所有内置命令名。
        """
        return WordCompleter([
            "/clear", "/save", "/tools", "/quit", "/help", "/exit",
        ])

    # ------------------------------------------------------------------
    # REPL 主循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """启动 REPL 主循环。

        流程：
        1. 显示欢迎信息
        2. 循环读取用户输入（prompt_toolkit 的 PromptSession）
        3. 以 / 开头的输入路由到 _handle_command（内置命令）
        4. 普通文本提交给 _execute_task（Agent 执行）
        5. 退出时调用 _handle_exit 保存会话并显示告别信息

        Ctrl+C（KeyboardInterrupt）仅中断当前输入行，不会退出 REPL。
        Ctrl+D 或输入 quit/exit 退出 REPL。
        """
        self._running = True
        self.display.show_welcome()

        while self._running:
            try:
                # 使用 prompt_async 以兼容 asyncio 事件循环
                user_input: str = await self._session.prompt_async(
                    [("class:prompt", "> ")],  # 提示符样式
                    multiline=False,  # 单行模式：Enter 直接提交，Esc+Enter 插入换行
                )
            except (EOFError, KeyboardInterrupt):
                # Ctrl+D → 退出 REPL；Ctrl+C → 退出 REPL（在空输入时）
                self._running = False
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # 检查退出关键词（直接输入 quit/exit，不以 / 开头）
            if user_input.lower() in ("quit", "exit"):
                self._running = False
                break

            # 检查内置命令（以 / 开头）
            if user_input.startswith("/"):
                await self._handle_command(user_input)
                continue

            # 提交给 Agent 执行
            # KeyboardInterrupt 在此级别捕获，仅中断当前任务不退出 REPL
            try:
                await self._execute_task(user_input)
            except KeyboardInterrupt:
                self.display.show_info("⏹️  Task interrupted (Ctrl+C)")
                continue

        # 退出：保存会话 + 显示统计
        self._handle_exit()

    # ------------------------------------------------------------------
    # 会话恢复
    # ------------------------------------------------------------------

    async def restore_session(self, session: AgentState | dict[str, Any]) -> None:
        """从保存的会话恢复对话上下文。

        将历史会话中的 messages 加载到 ``_conversation_history``，
        使后续的 Agent.run() 调用能够携带之前的对话历史，
        实现跨进程会话恢复。

        使用场景：``nexus --continue`` 命令。

        Parameters
        ----------
        session : AgentState | dict[str, Any]
            恢复的会话。可以是 AgentState 实例（从 SessionManager.load() 返回）
            或包含会话元数据的 dict（从 SessionManager.list_sessions() 返回）。

        Notes
        -----
        当前 Agent.run() 每次创建新的 AgentState，跨轮对话通过
        _conversation_history 在 REPL 层维护。未来若 Agent 支持
        initial_messages 参数（或 state 延续），可切换为更原生的机制。
        """
        if isinstance(session, AgentState):
            # AgentState 实例：直接提取 messages
            self._conversation_history = list(session.messages)
            self.display.show_info(
                f"已恢复会话，{len(session.messages)} 条历史消息"
            )
            logger.info(
                "Session restored from AgentState",
                extra={"message_count": len(session.messages)},
            )
        elif isinstance(session, dict):
            # dict 格式：尝试从 state.messages 中恢复
            state_data: dict[str, Any] | None = session.get("state")
            if state_data and isinstance(state_data, dict):
                messages: list[dict[str, Any]] = state_data.get("messages", [])
                self._conversation_history = messages
                self.display.show_info(
                    f"已恢复会话，{len(messages)} 条历史消息"
                )
                logger.info(
                    "Session restored from dict",
                    extra={"message_count": len(messages)},
                )
            else:
                self.display.show_info("会话数据为空，无法恢复上下文")
                logger.warning("Session dict has no state data")

    # ------------------------------------------------------------------
    # 任务执行
    # ------------------------------------------------------------------

    async def _execute_task(self, user_input: str) -> None:
        """执行用户任务 —— 流式展示 Agent 执行全过程。

        通过订阅 EventBus 来实现流式展示：
        - BEFORE_LLM_CALL → 显示 spinner "Thinking..."
        - AFTER_LLM_CALL → 展示 LLM 响应内容
        - BEFORE_TOOL_CALL → 显示工具调用信息
        - AFTER_TOOL_CALL → 显示工具执行结果
        - ON_FINISH → 显示执行摘要

        事件处理器在 Agent.run() 之前订阅、之后取消，避免跨任务泄露。
        """
        self.display.show_info("")  # 空行分隔

        # ---- 事件处理器 ----
        # 使用闭包在 handler 中引用外部变量（如 tool_count、token_usage）

        tool_count: int = 0
        token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        start_time: float = time.time()

        # ON_FINISH 标志：用于在主协程中等待 Run 完成
        finish_event: asyncio.Event = asyncio.Event()

        async def on_before_llm(event: Event) -> None:
            """LLM 调用前：显示思考 spinner。"""
            self.display.show_info("Thinking...")

        async def on_after_llm(event: Event) -> None:
            """LLM 调用后：展示响应内容并累计 token 用量。"""
            response = event.payload.get("response")
            usage = event.payload.get("usage")
            if usage:
                token_usage["prompt"] += usage.get("prompt_tokens", 0)
                token_usage["completion"] += usage.get("completion_tokens", 0)
                token_usage["total"] += usage.get("total_tokens", 0)
            # AI 回复用亮色渲染，区别于系统状态信息
            if response and hasattr(response, "content") and response.content:
                self.display.render_response(response.content)

        async def on_before_tool(event: Event) -> None:
            """工具调用前：展示工具名称和参数摘要。"""
            nonlocal tool_count
            tool_count += 1
            tool_name: str = event.payload.get("tool_name", "unknown")
            args: dict[str, Any] = event.payload.get("args", {})
            # 构建参数摘要（截断过长值）
            arg_parts: list[str] = []
            for k, v in args.items():
                v_str = str(v)
                if len(v_str) > 50:
                    v_str = v_str[:47] + "..."
                arg_parts.append(f"{k}={v_str}")
            arg_summary = ", ".join(arg_parts)
            self.display.show_info(f"[{tool_count}] 🔧 {tool_name}({arg_summary})")

        async def on_after_tool(event: Event) -> None:
            """工具调用后：展示执行结果。"""
            tool_name: str = event.payload.get("tool_name", "unknown")
            result = event.payload.get("result")
            error = event.payload.get("error")
            if error:
                self.display.render_warning(f"  ❌ {tool_name}: {error}")
            elif result is not None:
                result_str = str(result)
                if len(result_str) > 200:
                    result_str = result_str[:197] + "..."
                self.display.show_info(f"  ✅ {tool_name}: {result_str}")

        async def on_error(event: Event) -> None:
            """运行时错误：展示错误信息。"""
            error_info = event.payload.get("error")
            self.display.render_error(f"执行错误: {error_info}")

        async def on_finish(event: Event) -> None:
            """Run 完成：设置完成标志。"""
            finish_event.set()

        # ---- 订阅事件 ----
        await self.agent.events.subscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
        await self.agent.events.subscribe(EventType.AFTER_LLM_CALL, on_after_llm)
        await self.agent.events.subscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
        await self.agent.events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)
        await self.agent.events.subscribe(EventType.ON_ERROR, on_error)
        await self.agent.events.subscribe(EventType.ON_FINISH, on_finish)

        try:
            # 将跨轮对话历史传入 agent，保持多轮上下文
            state: AgentState = await self.agent.run(
                user_input,
                initial_messages=self._conversation_history if self._conversation_history else None,
            )

            # 将本轮对话追加到跨轮历史
            self._conversation_history.append({"role": "user", "content": user_input})
            for msg in state.messages:
                if msg.get("role") == "assistant":
                    self._conversation_history.append(msg)

            # 等待 ON_FINISH 事件（或超时）
            try:
                await asyncio.wait_for(finish_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass  # 事件可能已完成派发，继续处理

            logger.info(
                "Task executed",
                extra={
                    "steps": state.current_step,
                    "tools": tool_count,
                    "tokens": token_usage["total"],
                },
            )

        except Exception as e:
            self.display.render_error(f"执行错误: {e}")
            logger.error("Task execution failed", exc_info=True)

        finally:
            # ---- 取消订阅，防止跨任务事件泄露 ----
            await self.agent.events.unsubscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
            await self.agent.events.unsubscribe(EventType.AFTER_LLM_CALL, on_after_llm)
            await self.agent.events.unsubscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
            await self.agent.events.unsubscribe(EventType.AFTER_TOOL_CALL, on_after_tool)
            await self.agent.events.unsubscribe(EventType.ON_ERROR, on_error)
            await self.agent.events.unsubscribe(EventType.ON_FINISH, on_finish)

    # ------------------------------------------------------------------
    # 内置命令处理
    # ------------------------------------------------------------------

    async def _handle_command(self, cmd: str) -> None:
        """处理内置命令。

        支持的命令：
        /clear  - 清空对话上下文（重新创建 Agent，保留配置和工具）
        /save   - 手动保存当前会话
        /tools  - 列出当前已注册的工具
        /quit   - 退出 REPL
        /exit   - 退出 REPL（同 /quit）
        /help   - 显示帮助

        命令以 / 开头，与 opencode 风格一致。
        未知命令显示警告提示，引导用户使用 /help。

        Parameters
        ----------
        cmd : str
            用户输入的完整命令行（含前导 /）。
        """
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()

        if command in ("/quit", "/exit"):
            self._running = False

        elif command == "/clear":
            # 重新创建 Agent（保留 LLM、Policy、配置和工具）
            from nexus.cli.main import _register_tools

            original_agent = self.agent
            new_agent = Agent(
                llm=original_agent.llm,
                policy=original_agent.policy.__class__(
                    max_steps=self.config.max_steps
                ),
                system_prompt=self.config.system_prompt,
                max_steps=self.config.max_steps,
                name=original_agent.name,
            )
            # 根据配置文件重新注册工具
            _register_tools(new_agent, self.config)
            self.agent = new_agent
            self._conversation_history.clear()
            self.display.show_info("上下文已清空，工具已重新注册")
            logger.info("Agent recreated for /clear command")

        elif command == "/save":
            # 保存当前对话历史到会话管理器
            state = AgentState(
                task="repl session",
                messages=list(self._conversation_history),
            )
            session_id = self.session_mgr.save(
                state,
                metadata={"command": cmd, "mode": "repl"},
            )
            self.display.show_info(f"会话已保存 (id: {session_id})")
            logger.info(
                "Session saved via /save",
                extra={"session_id": session_id},
            )

        elif command == "/tools":
            # 列出已注册的工具
            tools = list(self.agent.tool_registry)
            if not tools:
                self.display.show_info("（暂无已注册的工具）")
            else:
                for t in tools:
                    desc = t.description[:60]
                    print(f"  🔧 {t.name}: {desc}")

        elif command == "/help":
            # 显示帮助信息
            print("  内置命令:")
            print("    /clear  清空对话上下文")
            print("    /save   保存当前会话")
            print("    /tools  列出已注册工具")
            print("    /quit   退出 REPL")
            print("    /help   显示此帮助")
            print("  ")
            print("  直接输入问题或任务描述即可与 Agent 对话。")
            print("  按 Ctrl+D 或输入 quit/exit 退出。")
            print("  ")

        else:
            self.display.render_warning(
                f"未知命令: {command}，输入 /help 查看帮助"
            )

    # ------------------------------------------------------------------
    # 退出处理
    # ------------------------------------------------------------------

    def _handle_exit(self) -> None:
        """REPL 退出时的清理工作。

        保存当前会话并显示告别信息。
        保存失败不中断退出流程，确保用户始终能正常退出。
        """
        # 保存会话（最佳努力，失败不影响退出）
        try:
            state = AgentState(
                task="repl session",
                messages=list(self._conversation_history),
            )
            self.session_mgr.save(state, metadata={"mode": "repl"})
        except Exception:
            logger.warning("Failed to save session on exit", exc_info=True)

        # 显示告别信息
        total_steps = sum(1 for _ in self._conversation_history) // 2
        self.display.show_goodbye(
            steps=total_steps,
            duration=0,  # 跨多轮对话的总耗时不易精确计算，暂用 0
        )
        logger.info("Repl exited")
