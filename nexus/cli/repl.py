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
        run_id: str | None = None,
        log_file: str | None = None,
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
        run_id : str | None
            本进程的会话 ID，用于覆盖式保存（同一 REPL 多次 /save 写同一 JSON）。
            None 时退化为原行为（每次 save 生成新 uuid）。
        log_file : str | None
            本进程的日志文件路径，写入会话 metadata.log_file，
            便于会话删除时同步清理日志。
        """
        self.agent = agent
        self.display = display
        self.config = config
        self.session_mgr = session_mgr or SessionManager()
        self._running = False
        # 用于覆盖式保存与日志关联
        self._run_id = run_id
        self._log_file = log_file

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
                "session_id": run_id,
            },
        )

    # ------------------------------------------------------------------
    # prompt_toolkit 配置
    # ------------------------------------------------------------------

    def _get_style(self) -> Style:
        """定义 prompt_toolkit 的样式。

        使用简洁的配色方案：
        - 提示符（❯ ）用绿色加粗，醒目但不刺眼
        """
        return Style.from_dict({
            "prompt": "#00aa00 bold",
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
            "/clear", "/save", "/tools", "/mcp", "/quit", "/help", "/exit",
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
        # CLI 启动即加载配置的 MCP server（安装失败不阻断 REPL）
        await self._install_mcp()

        while self._running:
            try:
                # 使用 prompt_async 以兼容 asyncio 事件循环
                user_input: str = await self._session.prompt_async(
                    [("class:prompt", "❯ ")],
                    multiline=False,
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
    # MCP 启动安装
    # ------------------------------------------------------------------

    async def _install_mcp(self) -> None:
        """启动时按配置安装 MCP 插件（连接 MCP server 并注册远端工具）。

        CLI 启动即加载配置的 mcp_servers；无启用项时 ``install_mcp``
        为 no-op（返回 None），安装异常仅记 warning —— MCP 失败
        不影响 REPL 正常启动。
        """
        try:
            from nexus.core.factory import install_mcp

            await install_mcp(self.agent, self.config)
        except Exception as exc:
            logger.warning("MCP 插件安装失败（REPL 启动）: %s", exc)

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
        """执行用户任务 —— 角色化展示 Agent 执行全过程。

        展示流程：
        1. 渲染用户消息 Panel（👤 You，绿色边框）→ 轮次分隔线 →
           AI 标签（🤖 Nexus），把用户输入固定在对话流中，
           避免随 prompt 滚动消失后与 AI 输出混淆
        2. 立即启动思考链 Live（🤔 Thinking 标题），让用户在首个 chunk
           到达前就感知"正在思考"状态
        3. 执行 Agent.run()，事件回调驱动实时展示：
           - LLM_CHUNK：增量渲染思考链（dim italic Panel）与回复（Markdown Live）
           - AFTER_LLM_CALL：关闭流式 Live、统计 token；
             非流式回退时一次性渲染思考链或回复
           - AFTER_TOOL_CALL：渲染工具调用 Panel（青色/红色边框）
           - ON_ERROR：关闭 Live、渲染错误 Panel（红色边框）
        4. 维护跨轮对话历史

        事件处理器在 Agent.run() 之前订阅、之后取消，避免跨任务泄露。
        """
        # 用户消息 Panel → 分隔线 → AI 标签（顺序固定，形成角色化对话流）
        self.display.render_user_message(user_input)
        self.display.render_divider()
        self.display.render_assistant_header()

        tool_count: int = 0
        token_usage: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        finish_event: asyncio.Event = asyncio.Event()

        # 流式状态（每轮 LLM 调用重置）
        # thinking 和 response 都用纯 append 模式，完全不使用 Live/Status：
        # - 无光标控制序列 → 滚动绝对安全
        # - thinking 增量 print（dim italic），response 按段落渲染 Markdown
        # - 不用 Panel 边框（Panel 需一次性渲染，与流式 append 冲突）
        stream_state: dict[str, Any] = {
            "thinking_started": False,
            "response_started": False,
            "thinking_acc": "",
            "response_acc": "",
        }

        async def on_llm_chunk(event: Event) -> None:
            """LLM chunk 到达：增量打印思考链与回复。

            thinking 用 append 模式直接 print（dim italic），首个 reasoning
            到达时先打印 🤔 Thinking 标题。response 用段落缓冲渲染 Markdown。
            两者均不使用 Live，不发送光标控制序列，滚动绝对安全。
            """
            delta_content = event.payload.get("delta_content", "")
            delta_reasoning = event.payload.get("delta_reasoning", "")

            if delta_reasoning:
                # 首个 reasoning chunk → 打印 Thinking 标题
                if not stream_state["thinking_started"]:
                    self.display.print_thinking_start()
                    stream_state["thinking_started"] = True
                stream_state["thinking_acc"] += delta_reasoning
                self.display.print_thinking_chunk(delta_reasoning)

            if delta_content:
                # thinking 结束 → 换行收尾
                if stream_state["thinking_started"]:
                    self.display.end_thinking_stream()
                    stream_state["thinking_started"] = False
                # response：append 模式按段落渲染 Markdown
                stream_state["response_acc"] += delta_content
                self.display.print_response_chunk(delta_content)
                stream_state["response_started"] = True

        async def on_after_llm(event: Event) -> None:
            """LLM 调用后：收尾流式状态，统计 token。

            流式路径下内容已通过 LLM_CHUNK 事件增量渲染完毕，
            此处仅做收尾（thinking/response 换行收尾、统计 token）。
            非流式路径下（stream=False）response_acc 为空，
            仍走一次性渲染逻辑。
            """
            # thinking 收尾（兜底纯 thinking 无 content 场景）
            if stream_state["thinking_started"]:
                self.display.end_thinking_stream()
                stream_state["thinking_started"] = False
            # response 收尾换行
            if stream_state["response_started"]:
                self.display.end_response_stream()
                stream_state["response_started"] = False

            response = event.payload.get("response")
            usage = event.payload.get("usage")
            if usage:
                token_usage["prompt"] += usage.get("prompt_tokens", 0)
                token_usage["completion"] += usage.get("completion_tokens", 0)
                token_usage["total"] += usage.get("total_tokens", 0)

            # 非流式回退：未走流式时一次性渲染
            if not stream_state["response_acc"] and response and hasattr(response, "content") and response.content:
                has_tool_calls = bool(getattr(response, "tool_calls", None))
                if has_tool_calls:
                    self.display.render_thinking(response.content)
                else:
                    self.display.render_response(response.content)
            # 流式已渲染 content 的场景：content 已显示，
            # 若同时存在 tool_calls 则由 on_after_tool 处理，此处无需重复渲染

            # 重置流式累积状态（为下一轮 LLM 调用准备）
            stream_state["thinking_acc"] = ""
            stream_state["response_acc"] = ""

        async def on_before_llm(event: Event) -> None:
            """LLM 调用前：无需操作（append 模式无需预创建 Live）。

            thinking 标题在首个 reasoning chunk 到达时由 on_llm_chunk 打印，
            此处保持空实现以兼容 ReAct 多轮循环的事件订阅。
            """
            pass

        async def on_before_tool(event: Event) -> None:
            """工具调用前：累加计数器（spinner 已显示工作状态，无需额外打印）。"""
            nonlocal tool_count
            tool_count += 1

        async def on_after_tool(event: Event) -> None:
            """工具调用后：渲染工具调用 Panel。"""
            tool_name: str = event.payload.get("tool_name", "unknown")
            args: dict[str, Any] = event.payload.get("args", {})
            result = event.payload.get("result")
            error = event.payload.get("error")
            if error:
                self.display.render_tool_call(
                    tool_name=tool_name,
                    args=args,
                    result=str(error)[:200],
                    success=False,
                    index=tool_count,
                )
            elif result is not None:
                result_str = str(result)
                if len(result_str) > 200:
                    result_str = result_str[:197] + "..."
                self.display.render_tool_call(
                    tool_name=tool_name,
                    args=args,
                    result=result_str,
                    success=True,
                    index=tool_count,
                )

        async def on_error(event: Event) -> None:
            """运行时错误：收尾流式状态并展示错误信息。"""
            if stream_state["thinking_started"]:
                self.display.end_thinking_stream()
                stream_state["thinking_started"] = False
            if stream_state["response_started"]:
                self.display.end_response_stream()
                stream_state["response_started"] = False
            error_info = event.payload.get("error")
            self.display.render_error(f"执行错误: {error_info}")

        async def on_finish(event: Event) -> None:
            """Run 完成：设置完成标志。"""
            finish_event.set()

        await self.agent.events.subscribe(EventType.AFTER_LLM_CALL, on_after_llm)
        await self.agent.events.subscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
        await self.agent.events.subscribe(EventType.LLM_CHUNK, on_llm_chunk)
        await self.agent.events.subscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
        await self.agent.events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)
        await self.agent.events.subscribe(EventType.ON_ERROR, on_error)
        await self.agent.events.subscribe(EventType.ON_FINISH, on_finish)

        try:
            # 流式模式下不使用 spinner：spinner 与 Rich Live 共享终端刷新会冲突
            state: AgentState = await self.agent.run(
                user_input,
                initial_messages=(
                    self._conversation_history if self._conversation_history else None
                ),
            )

            # 用 state.messages（含完整 user/assistant/tool）替换历史，
            # 跳过 system 消息（由 agent.run 重新注入）。
            # 这样 tool 角色消息得以保留，使 assistant 的 tool_calls 有对应结果。
            #
            # 重要：必须保留 ``role=="tool"`` 消息（带 ``tool_call_id`` 字段），
            # 否则下一轮 ``AnthropicLLM._split_system_messages`` 无法将 tool result
            # 转换为 Anthropic 的 tool_result blocks，assistant 的 tool_calls
            # 链路会断裂 —— 当前 test_cli_session.py 与 test_runtime.py 已覆盖。
            self._conversation_history = [
                msg for msg in state.messages if msg.get("role") != "system"
            ]

            try:
                await asyncio.wait_for(finish_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

            logger.info(
                "Task executed",
                extra={
                    "steps": state.current_step,
                    "tools": tool_count,
                    "tokens": token_usage["total"],
                },
            )

        except Exception as e:
            # 兜底收尾流式状态
            if stream_state["thinking_started"]:
                self.display.end_thinking_stream()
                stream_state["thinking_started"] = False
            if stream_state["response_started"]:
                self.display.end_response_stream()
                stream_state["response_started"] = False
            self.display.render_error(f"执行错误: {e}")
            logger.error("Task execution failed", exc_info=True)

        finally:
            await self.agent.events.unsubscribe(EventType.AFTER_LLM_CALL, on_after_llm)
            await self.agent.events.unsubscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
            await self.agent.events.unsubscribe(EventType.LLM_CHUNK, on_llm_chunk)
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
        /mcp    - 管理 MCP server（list/tools/add/remove/enable/disable/reconnect）
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
            from nexus.core.factory import register_tools

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
            register_tools(new_agent, self.config)
            self.agent = new_agent
            # 重新接入 MCP 工具：register_tools 只注册内置工具，
            # 不重装 MCPPlugin 会丢失全部 mcp__ 前缀工具
            if self.config.mcp_servers:
                from nexus.core.factory import install_mcp
                try:
                    await install_mcp(new_agent, self.config)
                except Exception as exc:
                    logger.warning("MCP 重装失败（/clear）: %s", exc)
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
                # 传入 run_id 实现覆盖式保存：同一 REPL 多次 /save 写同一 JSON
                session_id=self._run_id,
                log_file=self._log_file,
            )
            self.display.show_info(f"会话已保存 (id: {session_id})")
            logger.info(
                "Session saved via /save",
                extra={"session_id": session_id},
            )

        elif command == "/tools":
            # 列出已注册的工具（Rich Table 渲染）
            self.display.render_tools_table(list(self.agent.tool_registry))

        elif command == "/mcp":
            # MCP server 管理（list/tools/add/remove/enable/disable/reconnect）
            await self._handle_mcp_command(parts[1] if len(parts) > 1 else "")

        elif command == "/help":
            # 显示帮助信息（Rich Panel 渲染）
            self.display.render_help_panel()

        else:
            self.display.render_warning(
                f"未知命令: {command}，输入 /help 查看帮助"
            )

    # ------------------------------------------------------------------
    # /mcp 命令族
    # ------------------------------------------------------------------

    @staticmethod
    def _mcp_usage() -> str:
        """返回 /mcp 命令族的用法说明文本。"""
        return (
            "用法:\n"
            "  /mcp [list]                         列出所有 MCP server\n"
            "  /mcp tools <名称>                   列出该 server 的远端工具\n"
            "  /mcp add <名称> <命令> [参数...]    添加 stdio server\n"
            "  /mcp add <名称> --url <url>         添加 http server\n"
            "  /mcp remove <名称>                  移除 server（断开并注销工具）\n"
            "  /mcp enable <名称>                  启用 server（重连并注册工具）\n"
            "  /mcp disable <名称>                 禁用 server（断开并注销工具）\n"
            "  /mcp reconnect <名称>               重连 server 并刷新工具"
        )

    def _get_mcp_plugin(self) -> Any | None:
        """从当前 Agent 的插件注册中心获取 MCPPlugin，未安装返回 None。"""
        return self.agent.plugin_registry.get("mcp")

    async def _reload_or_install_mcp(self) -> None:
        """按当前配置热生效 MCP：已安装插件则全量 reload，否则安装插件。

        用于 add / enable / disable 后的配置热更新。无启用的 server 时
        ``install_mcp`` 为 no-op（返回 None），行为与之前一致。
        """
        plugin = self._get_mcp_plugin()
        if plugin is not None:
            await plugin.reload(self.config.mcp_servers)
        else:
            from nexus.core.factory import install_mcp
            await install_mcp(self.agent, self.config)

    def _save_config_quietly(self) -> None:
        """持久化当前配置到用户级 ~/.nexus/nexus.yaml，失败仅提示不阻断。"""
        from nexus.cli.config import save_config

        try:
            save_config(self.config)
        except Exception as exc:
            self.display.render_warning(f"配置保存失败: {exc}")
            logger.warning("Failed to save config after /mcp change", exc_info=True)

    def _mcp_status_list(self) -> list[dict[str, Any]]:
        """汇总 MCP server 状态：manager 运行时状态 + 配置中未接管的条目。

        manager 未安装（如启动时无启用 server）时，配置中的 server 以
        disabled/disconnected 形态补入列表，保证 ``/mcp list`` 始终
        反映配置文件的真实内容。
        """
        plugin = self._get_mcp_plugin()
        status_list: list[dict[str, Any]] = (
            plugin.manager.get_status() if plugin is not None else []
        )
        known = {s["name"] for s in status_list}
        for name, cfg in self.config.mcp_servers.items():
            if name in known:
                continue
            status_list.append({
                "name": name,
                "transport": cfg.transport,
                "enabled": cfg.enabled,
                "status": "disabled" if not cfg.enabled else "disconnected",
                "error": None,
                "tool_count": 0,
                "tools": [],
            })
        return status_list

    async def _handle_mcp_command(self, args: str) -> None:
        """处理 /mcp 子命令（MCP server 的查看与热管理）。

        热生效路径：
        - add / enable / disable：修改 ``config.mcp_servers`` 并
          ``save_config`` 持久化后，经 ``_reload_or_install_mcp`` 全量
          重载（插件不存在时走 ``factory.install_mcp`` 首次安装）；
        - remove：``manager.remove(name, registry)`` 断开并注销工具后
          从配置移除并持久化；
        - reconnect：``manager.reconnect(name, registry)`` 单点重连刷新工具。

        Parameters
        ----------
        args : str
            /mcp 之后的参数串（可能为空，等价于 ``list``）。
        """
        from nexus.cli.config import MCPServerConfig

        tokens = args.split()
        sub = tokens[0].lower() if tokens else "list"

        if sub == "list":
            self.display.render_mcp_servers(self._mcp_status_list())

        elif sub == "tools":
            if len(tokens) != 2:
                self.display.show_info(self._mcp_usage())
                return
            name = tokens[1]
            status = next(
                (s for s in self._mcp_status_list() if s["name"] == name), None,
            )
            if status is None:
                self.display.render_warning(f"未找到 MCP server: {name}")
                return
            self.display.render_mcp_tools(name, status.get("tools", []))

        elif sub == "add":
            if len(tokens) < 3:
                self.display.show_info(self._mcp_usage())
                return
            name = tokens[1]
            if name in self.config.mcp_servers:
                self.display.render_warning(f"MCP server 已存在: {name}")
                return
            if tokens[2] == "--url":
                # http 模式：/mcp add <名称> --url <url>
                if len(tokens) != 4:
                    self.display.show_info(self._mcp_usage())
                    return
                cfg = MCPServerConfig(url=tokens[3])
            else:
                # stdio 模式：/mcp add <名称> <命令> [参数...]
                cfg = MCPServerConfig(command=tokens[2], args=tokens[3:])
            self.config.mcp_servers[name] = cfg
            self._save_config_quietly()
            self.display.show_info(f"正在连接 MCP server '{name}'...")
            await self._reload_or_install_mcp()
            status = next(
                (s for s in self._mcp_status_list() if s["name"] == name), None,
            )
            if status and status["status"] == "connected":
                self.display.show_info(
                    f"已添加 MCP server '{name}'，注册 {status['tool_count']} 个工具"
                )
            elif status and status["status"] == "error":
                self.display.render_warning(
                    f"MCP server '{name}' 已保存但连接失败: {status['error']}"
                )
            else:
                self.display.show_info(f"已添加 MCP server '{name}'")
            logger.info("MCP server added via /mcp add", extra={"name": name})

        elif sub == "remove":
            if len(tokens) != 2:
                self.display.show_info(self._mcp_usage())
                return
            name = tokens[1]
            if name not in self.config.mcp_servers:
                self.display.render_warning(f"未找到 MCP server: {name}")
                return
            plugin = self._get_mcp_plugin()
            if plugin is not None:
                await plugin.manager.remove(name, self.agent.tool_registry)
            del self.config.mcp_servers[name]
            self._save_config_quietly()
            self.display.show_info(f"已移除 MCP server '{name}'")
            logger.info("MCP server removed via /mcp remove", extra={"name": name})

        elif sub in ("enable", "disable"):
            if len(tokens) != 2:
                self.display.show_info(self._mcp_usage())
                return
            name = tokens[1]
            cfg = self.config.mcp_servers.get(name)
            if cfg is None:
                self.display.render_warning(f"未找到 MCP server: {name}")
                return
            cfg.enabled = sub == "enable"
            self._save_config_quietly()
            await self._reload_or_install_mcp()
            if cfg.enabled:
                status = next(
                    (s for s in self._mcp_status_list() if s["name"] == name), None,
                )
                if status and status["status"] == "connected":
                    self.display.show_info(
                        f"已启用 MCP server '{name}'，注册 {status['tool_count']} 个工具"
                    )
                elif status and status["status"] == "error":
                    self.display.render_warning(
                        f"MCP server '{name}' 已启用但连接失败: {status['error']}"
                    )
                else:
                    self.display.show_info(f"已启用 MCP server '{name}'")
            else:
                self.display.show_info(f"已禁用 MCP server '{name}'，工具已注销")
            logger.info(
                "MCP server toggled via /mcp",
                extra={"name": name, "enabled": cfg.enabled},
            )

        elif sub == "reconnect":
            if len(tokens) != 2:
                self.display.show_info(self._mcp_usage())
                return
            name = tokens[1]
            plugin = self._get_mcp_plugin()
            if plugin is None:
                self.display.render_warning(
                    "MCP 插件未安装（当前无启用的 server），无法重连"
                )
                return
            ok = await plugin.manager.reconnect(name, self.agent.tool_registry)
            if ok:
                status = next(
                    (s for s in plugin.manager.get_status() if s["name"] == name),
                    None,
                )
                count = status["tool_count"] if status else 0
                self.display.show_info(
                    f"已重连 MCP server '{name}'，注册 {count} 个工具"
                )
            else:
                self.display.render_warning(f"重连失败: {name}")
            logger.info(
                "MCP server reconnected via /mcp reconnect",
                extra={"name": name, "ok": ok},
            )

        else:
            self.display.render_warning(f"未知 /mcp 子命令: {sub}")
            self.display.show_info(self._mcp_usage())

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
            self.session_mgr.save(
                state,
                metadata={"mode": "repl"},
                session_id=self._run_id,
                log_file=self._log_file,
            )
        except Exception:
            logger.warning("Failed to save session on exit", exc_info=True)

        # 显示告别信息
        total_steps = sum(1 for _ in self._conversation_history) // 2
        self.display.show_goodbye(
            steps=total_steps,
            duration=0,  # 跨多轮对话的总耗时不易精确计算，暂用 0
        )
        logger.info("Repl exited")
