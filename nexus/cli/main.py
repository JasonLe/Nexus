"""Nexus CLI 主入口 —— argparse 参数解析和命令派发。

设计思路
--------
main() 是 CLI 的统一入口：
- ``nexus "prompt"`` → 直接执行单次任务
- ``nexus`` (无参数) → 进入 REPL 交互模式
- ``nexus --list-sessions`` → 列出历史会话
- ``nexus --continue`` → 恢复最近会话

设计原因：参考 opencode/pi 的模式，让 CLI 同时支持"一次性问答"
和"持续对话"两种使用方式。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from nexus.core.agent.agent import Agent
from nexus.llm.base import BaseLLM
from nexus.llm.providers.openai import OpenAILLM
from nexus.cli.config import (
    NexusConfig,
    load_config,
    save_config,
)
from nexus.logging import NexusFormatter, get_logger

logger = get_logger(__name__)


def setup_logging(
    verbose: bool = False,
    debug: bool = False,
    session_id: str | None = None,
    log_dir: str | None = None,
) -> str:
    """配置 CLI 日志（终端 + 文件持久化）。

    终端日志级别由 verbose/debug 控制：
    - debug=True:  DEBUG 级别
    - verbose=True: INFO 级别
    - 默认: WARNING 级别

    文件日志始终输出 DEBUG 级别，按会话命名存储在
    ``~/.nexus/logs/<session_id>.log``，与 ``~/.nexus/sessions/<session_id>.json``
    一一对应，便于会话删除时同步清理日志。

    Parameters
    ----------
    verbose : bool
        终端显示 INFO 级别日志。
    debug : bool
        终端显示 DEBUG 级别日志（覆盖 verbose）。
    session_id : str | None
        会话 ID，用作日志文件名。None 时自动生成 8 位 uuid 前缀。
    log_dir : str | None
        日志根目录，None 时默认 ``~/.nexus/logs/``。

    Returns
    -------
    str
        实际写入的日志文件绝对路径，供调用方写入会话 metadata。
    """
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)

    nexus_logger = logging.getLogger("nexus")
    nexus_logger.handlers.clear()
    formatter = NexusFormatter()

    # 终端日志
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)
    nexus_logger.addHandler(stream_handler)

    # 文件日志 —— 按会话命名，平铺存储在 ~/.nexus/logs/<session_id>.log
    # 与 ~/.nexus/sessions/<session_id>.json 一一对应
    resolved_session_id = session_id or str(uuid.uuid4())[:8]
    log_root = Path(log_dir) if log_dir else (Path.home() / ".nexus" / "logs")
    log_root.mkdir(parents=True, exist_ok=True)
    log_file_path = log_root / f"{resolved_session_id}.log"

    # 单会话日志通常 < 1MB，使用普通 FileHandler 即可；
    # 多会话隔离已通过分文件解决，无需 RotatingFileHandler
    file_handler = logging.FileHandler(
        log_file_path, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    nexus_logger.addHandler(file_handler)
    nexus_logger.setLevel(logging.DEBUG)

    logger.debug(
        "Logging configured: terminal_level=%s, file=%s",
        logging.getLevelName(level),
        log_file_path,
    )
    return str(log_file_path)


def _create_llm(config: NexusConfig) -> BaseLLM:
    """根据 config 创建对应的 LLM 实例。

    Provider 工厂模式——根据 config.default_provider 动态选择，
    传入 config.provider_config 中的 api_key、model、base_url、
    context_window_tokens、timeout、max_retries。
    """
    provider = config.default_provider.lower()
    pc = config.provider_config

    # 公共参数：超时、重试、context window
    common_kwargs = {
        "context_window_tokens": pc.context_window_tokens,
    }

    if provider == "minimax":
        from nexus.llm.providers.minimax import MiniMaxAnthropicLLM
        return MiniMaxAnthropicLLM(
            api_key=pc.api_key,
            model=pc.model or "MiniMax-Text-01",
            base_url=pc.base_url or "https://api.minimaxi.com/anthropic",
            **common_kwargs,
        )
    elif provider == "anthropic":
        from nexus.llm.providers.anthropic import AnthropicLLM
        return AnthropicLLM(
            api_key=pc.api_key,
            model=pc.model or "claude-sonnet-4-20250514",
            **({"base_url": pc.base_url} if pc.base_url else {}),
            **common_kwargs,
        )
    else:
        return OpenAILLM(
            api_key=pc.api_key,
            base_url=pc.base_url,
            model=pc.model or "gpt-4o-mini",
            **common_kwargs,
        )


def _register_tools(agent: Agent, config: NexusConfig) -> None:
    """根据 config.tools.enabled 注册工具。

    如果 config.tools.enabled 为空列表 → 注册所有内置工具（默认行为）。
    否则仅注册 enabled 中列出的工具。
    """
    try:
        from nexus.cli.tools.file_tools import (
            ReadFileTool,
            WriteFileTool,
            ListDirTool,
            SearchContentTool,
        )
        from nexus.cli.tools.shell_tool import ShellTool
        all_tools = {
            "read_file": ReadFileTool(work_dir=config.work_dir or os.getcwd()),
            "write_file": WriteFileTool(work_dir=config.work_dir or os.getcwd()),
            "list_dir": ListDirTool(),
            "search_content": SearchContentTool(),
            "shell": ShellTool(work_dir=config.work_dir or os.getcwd()),
        }
        enabled = set(config.tools.enabled) if config.tools.enabled else set(all_tools.keys())

        for name, tool in all_tools.items():
            if name in enabled:
                agent.register_tool(tool)
                logger.debug("Tool registered: %s", name)
    except ImportError:
        logger.debug("File tools not available")


def _run_single(agent: Agent, config: NexusConfig, prompt: str) -> None:
    """直接执行模式：运行一次任务后退出。"""

    async def _run() -> None:
        try:
            from nexus.cli.display import DisplayManager
        except ImportError:
            state = await agent.run(prompt)
            if state.messages:
                last_msg = state.messages[-1]
                if last_msg.get("role") == "assistant":
                    print(last_msg.get("content", ""))
            return

        display = DisplayManager()
        from nexus.core.event.event_types import EventType
        from nexus.core.event.types import Event

        display.render_divider()
        display.render_assistant_header()

        tool_count = 0

        # 流式状态（每轮 LLM 调用重置）：thinking/response 的 Live 实例与累积文本
        stream_state: dict[str, Any] = {
            "thinking_live": None,
            "response_live": None,
            "thinking_acc": "",
            "response_acc": "",
        }

        async def on_llm_chunk(event: Event) -> None:
            """LLM chunk 到达：增量渲染思考链或回复。"""
            delta_content = event.payload.get("delta_content", "")
            delta_reasoning = event.payload.get("delta_reasoning", "")

            if delta_reasoning:
                if stream_state["thinking_live"] is None:
                    stream_state["thinking_live"] = display.start_streaming_thinking()
                stream_state["thinking_acc"] += delta_reasoning
                display.update_streaming_thinking(
                    stream_state["thinking_live"], stream_state["thinking_acc"])

            if delta_content:
                # thinking 结束后切换到 response：关闭 thinking Live
                if stream_state["thinking_live"] is not None:
                    stream_state["thinking_live"].stop()
                    stream_state["thinking_live"] = None
                if stream_state["response_live"] is None:
                    stream_state["response_live"] = display.start_streaming_response()
                stream_state["response_acc"] += delta_content
                display.update_streaming_response(
                    stream_state["response_live"], stream_state["response_acc"])

        async def on_after_llm(event) -> None:
            """LLM 调用后：关闭流式 Live，统计 token，避免重复渲染。"""
            # 关闭可能未关闭的 Live 实例
            if stream_state["thinking_live"] is not None:
                stream_state["thinking_live"].stop()
                stream_state["thinking_live"] = None
            if stream_state["response_live"] is not None:
                stream_state["response_live"].stop()
                stream_state["response_live"] = None

            payload = event.payload
            response = payload.get("response")
            # 非流式回退：未走流式时一次性渲染
            if not stream_state["response_acc"]:
                if response and hasattr(response, "content") and response.content:
                    has_tool_calls = bool(getattr(response, "tool_calls", None))
                    if has_tool_calls:
                        display.render_thinking(response.content)
                    else:
                        display.render_response(response.content)
                elif payload.get("content"):
                    display.render_response(payload["content"])
            # 流式已渲染 content 的场景：content 已显示，无需重复渲染

            # 重置流式累积状态（为下一轮 LLM 调用准备）
            stream_state["thinking_acc"] = ""
            stream_state["response_acc"] = ""

        async def on_before_tool(event) -> None:
            nonlocal tool_count
            tool_count += 1

        async def on_after_tool(event) -> None:
            payload = event.payload
            tool_name = payload.get("tool_name", "unknown")
            args = payload.get("args", {})
            result = payload.get("result", "")
            error = payload.get("error")
            if error:
                display.render_tool_call(
                    tool_name, args, str(error)[:200],
                    success=False, index=tool_count,
                )
            elif result is not None:
                display.render_tool_call(
                    tool_name, args, str(result)[:200],
                    success=True, index=tool_count,
                )

        await agent.events.subscribe(EventType.AFTER_LLM_CALL, on_after_llm)
        await agent.events.subscribe(EventType.LLM_CHUNK, on_llm_chunk)
        await agent.events.subscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
        await agent.events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)

        try:
            # 流式模式下不使用 spinner：spinner 与 Rich Live 共享终端刷新会冲突
            await agent.run(prompt)
        finally:
            await agent.events.unsubscribe(EventType.AFTER_LLM_CALL, on_after_llm)
            await agent.events.unsubscribe(EventType.LLM_CHUNK, on_llm_chunk)
            await agent.events.unsubscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
            await agent.events.unsubscribe(EventType.AFTER_TOOL_CALL, on_after_tool)

    asyncio.run(_run())


def _run_continue(
    agent: Agent,
    config: NexusConfig,
    run_id: str | None = None,
    log_file: str | None = None,
) -> None:

    async def _run() -> None:
        try:
            from nexus.cli.session import SessionManager
        except ImportError:
            print("Session manager is not available yet.")
            return

        mgr = SessionManager()
        _, session = mgr.load_latest()
        if session is None:
            print("No previous session found to continue.")
            return

        print("Continuing session...")
        try:
            from nexus.cli.repl import Repl
            from nexus.cli.display import DisplayManager
        except ImportError:
            print("REPL is not available yet.")
            return

        display = DisplayManager()
        # 新进程使用新 run_id（新日志文件），旧 session JSON 与旧日志保留不动
        repl = Repl(
            agent=agent, display=display, config=config,
            run_id=run_id, log_file=log_file,
        )
        await repl.restore_session(session)
        await repl.run()

    asyncio.run(_run())


def main() -> None:
    # 早期派发：nexus sessions <sub> [args]
    # 与位置参数 prompt 不兼容（argparse subparsers 会把 "sessions" 当成 prompt），
    # 因此在 argparse 解析前手动拦截
    if len(sys.argv) > 1 and sys.argv[1] == "sessions":
        _run_sessions_command(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus CLI Agent - 命令行编程助理",
    )
    parser.add_argument("prompt", nargs="?", help="直接执行的任务描述")
    parser.add_argument("--model", help="LLM 模型名称")
    parser.add_argument(
        "--provider", help="LLM Provider (openai|anthropic|minimax)，默认: openai"
    )
    parser.add_argument("--api-key", help="API Key")
    parser.add_argument("--base-url", help="API Base URL")
    parser.add_argument("--system-prompt", help="系统提示词")
    parser.add_argument("--max-steps", type=int, help="最大执行步数")
    parser.add_argument("--max-tokens", type=int, help="单次 LLM 调用最大输出 token 数")
    parser.add_argument("--work-dir", help="工作目录")
    parser.add_argument(
        "--continue", dest="continue_session", action="store_true",
        help="恢复最近会话",
    )
    parser.add_argument(
        "--list-sessions", action="store_true", help="列出历史会话（推荐使用 `nexus sessions list`）",
    )
    parser.add_argument(
        "--save-config", action="store_true",
        help="退出前将当前配置写入 ~/.nexus/nexus.yaml",
    )
    parser.add_argument(
        "--init-config", action="store_true",
        help="在当前目录生成 nexus.yaml 模板文件",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")
    parser.add_argument("--debug", action="store_true", help="显示调试日志")

    args = parser.parse_args()

    # --init-config 生成完整带注释的模板后退出
    if args.init_config:
        from nexus.cli.config import generate_config_template
        path = str(Path(os.getcwd()) / "nexus.yaml")
        if os.path.exists(path):
            print(f"File already exists: {path}")
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(generate_config_template())
        print(f"Config template created: {path}")
        print("Fill in your API keys, then run `nexus` to start.")
        return

    # 日志配置 —— 生成 run_id 用于「日志文件名 = 会话 JSON 文件名」
    run_id = str(uuid.uuid4())[:8]
    log_file = setup_logging(
        verbose=args.verbose, debug=args.debug, session_id=run_id,
    )

    # --list-sessions —— 复用 _sessions_list 的 Rich 表格渲染（向后兼容）
    if args.list_sessions:
        try:
            from nexus.cli.session import SessionManager
            from nexus.cli.display import DisplayManager
        except ImportError:
            print("Session manager is not available yet.")
            return
        mgr = SessionManager()
        display = DisplayManager()
        _sessions_list(mgr, display)
        return

    # 加载配置
    work_dir = args.work_dir or os.getcwd()
    cli_args = {k: v for k, v in vars(args).items() if v is not None}
    config = load_config(cli_args, work_dir=work_dir)
    config.work_dir = work_dir

    # 创建 LLM + Agent
    llm = _create_llm(config)
    agent = Agent(
        llm=llm,
        system_prompt=config.system_prompt,
        max_steps=config.max_steps,
    )

    # 注册工具（根据配置文件过滤）
    _register_tools(agent, config)

    # 派发
    try:
        if args.continue_session:
            _run_continue(agent, config, run_id=run_id, log_file=log_file)
        elif args.prompt:
            _run_single(agent, config, args.prompt)
        else:
            from nexus.cli.repl import Repl
            from nexus.cli.display import DisplayManager

            display = DisplayManager()
            repl = Repl(
                agent=agent, display=display, config=config,
                run_id=run_id, log_file=log_file,
            )
            asyncio.run(repl.run())
    finally:
        if args.save_config:
            try:
                saved_path = save_config(config)
                print(f"Config saved to {saved_path}")
            except Exception as e:
                logger.warning("Failed to save config: %s", e)


# ------------------------------------------------------------------
# nexus sessions 子命令
# ------------------------------------------------------------------


def _run_sessions_command(argv: list[str]) -> None:
    """``nexus sessions`` 子命令派发器。

    支持三个子命令：
    - ``nexus sessions [list]`` —— 列出历史会话（默认行为）
    - ``nexus sessions delete <id>`` —— 删除指定会话（带二次确认）
    - ``nexus sessions restore <id>`` —— 恢复指定会话并启动 REPL（带二次确认）
    """
    parser = argparse.ArgumentParser(
        prog="nexus sessions",
        description="管理历史会话：列出、删除、恢复",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<command>")

    # list（无子命令时的默认行为）
    sub.add_parser("list", help="列出历史会话")

    # delete
    p_del = sub.add_parser("delete", help="删除指定会话")
    p_del.add_argument("session_id", help="要删除的会话 ID")
    p_del.add_argument(
        "-y", "--yes", action="store_true",
        help="跳过确认直接删除",
    )
    p_del.add_argument(
        "--keep-logs", action="store_true",
        help="保留日志文件，仅删除会话 JSON",
    )

    # restore
    p_res = sub.add_parser("restore", help="恢复指定会话")
    p_res.add_argument("session_id", help="要恢复的会话 ID")
    p_res.add_argument(
        "-y", "--yes", action="store_true",
        help="跳过确认直接恢复",
    )

    args = parser.parse_args(argv)
    subcmd = args.subcommand or "list"  # 无子命令时默认 list

    # sessions 命令本身也写日志（临时 run_id），便于审计 sessions 操作
    setup_logging()

    from nexus.cli.session import SessionManager
    from nexus.cli.display import DisplayManager
    mgr = SessionManager()
    display = DisplayManager()

    if subcmd == "list":
        _sessions_list(mgr, display)
    elif subcmd == "delete":
        _sessions_delete(
            mgr, args.session_id,
            confirm=not args.yes,
            keep_logs=args.keep_logs,
            display=display,
        )
    elif subcmd == "restore":
        _sessions_restore(
            mgr, args.session_id,
            confirm=not args.yes,
            display=display,
        )


def _sessions_list(mgr: "SessionManager", display: DisplayManager) -> None:
    """列出历史会话（Rich 表格展示）。"""
    sessions = mgr.list_sessions()
    if not sessions:
        print("No saved sessions.")
        return
    display.render_sessions_table(sessions)
    print(
        f"\n共 {len(sessions)} 条会话。"
        "使用 `nexus sessions restore <id>` 恢复，"
        "`nexus sessions delete <id>` 删除。"
    )


def _sessions_delete(
    mgr: "SessionManager",
    session_id: str,
    confirm: bool,
    keep_logs: bool,
    display: DisplayManager,
) -> None:
    """删除指定会话（带二次确认）。"""
    # 1. 预览：加载会话信息展示给用户
    state = mgr.load(session_id)
    if state is None:
        print(f"Session not found: {session_id}")
        return

    sessions = mgr.list_sessions()
    info = next((s for s in sessions if s["id"] == session_id), None)
    summary = info["summary"] if info else "(unknown)"
    msg_count = info["message_count"] if info else len(state.messages)
    log_file = info.get("log_file") if info else None

    print(f"\n  ID:       {session_id}")
    print(f"  Summary:  {summary}")
    print(f"  Messages: {msg_count}")
    if log_file:
        print(f"  Log file: {log_file}")
    print()

    # 2. 确认
    if confirm:
        answer = input(
            f"Delete session {session_id}? This cannot be undone. (y/N) "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    # 3. 执行删除
    ok = mgr.delete(session_id, delete_logs=not keep_logs)
    if ok:
        print(f"Session {session_id} deleted.")
        if keep_logs:
            print("(Logs retained per --keep-logs)")
    else:
        print(f"Failed to delete session {session_id}.")


def _sessions_restore(
    mgr: "SessionManager",
    session_id: str,
    confirm: bool,
    display: DisplayManager,
) -> None:
    """恢复指定会话并启动 REPL（带二次确认）。"""
    state = mgr.load(session_id)
    if state is None:
        print(f"Session not found: {session_id}")
        return

    sessions = mgr.list_sessions()
    info = next((s for s in sessions if s["id"] == session_id), None)
    summary = info["summary"] if info else ""
    msg_count = info["message_count"] if info else len(state.messages)

    print(f"\n  ID:       {session_id}")
    print(f"  Summary:  {summary}")
    print(f"  Messages: {msg_count}")
    print()

    if confirm:
        answer = input(
            f"Restore session {session_id} and start REPL? (y/N) "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    # 3. 启动 REPL 并恢复（使用新 run_id 写新日志，旧 session JSON 不动）
    # 函数内部局部 import 避免循环依赖
    from nexus.cli.config import load_config as _load_config
    from nexus.cli.repl import Repl

    work_dir = os.getcwd()
    config = _load_config(work_dir=work_dir)
    config.work_dir = work_dir

    new_run_id = str(uuid.uuid4())[:8]
    new_log_file = setup_logging(session_id=new_run_id)

    llm = _create_llm(config)
    agent = Agent(
        llm=llm,
        system_prompt=config.system_prompt,
        max_steps=config.max_steps,
    )
    _register_tools(agent, config)

    display.show_welcome()
    repl = Repl(
        agent=agent, display=display, config=config,
        run_id=new_run_id, log_file=new_log_file,
    )

    async def _run() -> None:
        await repl.restore_session(state)
        await repl.run()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
