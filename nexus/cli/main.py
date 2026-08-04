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
    """根据 config 创建对应的 LLM 实例（薄包装，委托给 nexus.core.factory）。"""
    from nexus.core.factory import create_llm
    return create_llm(config)


def _register_tools(agent: Agent, config: NexusConfig) -> None:
    """根据 config.tools.enabled 注册工具（薄包装，委托给 nexus.core.factory）。"""
    from nexus.core.factory import register_tools
    register_tools(agent, config)


def _run_single(agent: Agent, config: NexusConfig, prompt: str) -> None:
    """直接执行模式：运行一次任务后退出。"""

    async def _run() -> None:
        # 在事件循环内安装 MCP 插件（无启用 server 时为 no-op）
        from nexus.core.factory import install_mcp
        await install_mcp(agent, config)

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

        # 与 repl.py 保持一致的渲染顺序：用户消息 Panel → 分隔线 → AI 标签
        display.render_user_message(prompt)
        display.render_divider()
        display.render_assistant_header()

        tool_count = 0

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
                    display.print_thinking_start()
                    stream_state["thinking_started"] = True
                stream_state["thinking_acc"] += delta_reasoning
                display.print_thinking_chunk(delta_reasoning)

            if delta_content:
                # thinking 结束 → 换行收尾
                if stream_state["thinking_started"]:
                    display.end_thinking_stream()
                    stream_state["thinking_started"] = False
                # response：append 模式按段落渲染 Markdown
                stream_state["response_acc"] += delta_content
                display.print_response_chunk(delta_content)
                stream_state["response_started"] = True

        async def on_after_llm(event) -> None:
            """LLM 调用后：收尾流式状态，统计 token。"""
            # thinking 收尾（兜底纯 thinking 无 content 场景）
            if stream_state["thinking_started"]:
                display.end_thinking_stream()
                stream_state["thinking_started"] = False
            # response 收尾换行
            if stream_state["response_started"]:
                display.end_response_stream()
                stream_state["response_started"] = False

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

        async def on_before_llm(event) -> None:
            """LLM 调用前：无需操作（append 模式无需预创建 Live）。

            thinking 标题在首个 reasoning chunk 到达时由 on_llm_chunk 打印，
            此处保持空实现以兼容 ReAct 多轮循环的事件订阅。
            """
            pass

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
        await agent.events.subscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
        await agent.events.subscribe(EventType.LLM_CHUNK, on_llm_chunk)
        await agent.events.subscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
        await agent.events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)

        try:
            await agent.run(prompt)
        except Exception as e:
            # 兜底收尾流式状态
            if stream_state["thinking_started"]:
                display.end_thinking_stream()
                stream_state["thinking_started"] = False
            if stream_state["response_started"]:
                display.end_response_stream()
                stream_state["response_started"] = False
            display.render_error(f"执行错误: {e}")
            logger.error("Single run failed", exc_info=True)
        finally:
            # 兜底收尾（异常路径下 except 已处理，正常路径下 on_after_llm 已处理）
            if stream_state["thinking_started"]:
                display.end_thinking_stream()
                stream_state["thinking_started"] = False
            if stream_state["response_started"]:
                display.end_response_stream()
                stream_state["response_started"] = False
            await agent.events.unsubscribe(EventType.AFTER_LLM_CALL, on_after_llm)
            await agent.events.unsubscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
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

    # 早期派发：nexus serve / nexus ui（同理避免被 argparse 当成 prompt）
    if len(sys.argv) > 1 and sys.argv[1] in ("serve", "ui"):
        _run_server_command(sys.argv[1], sys.argv[2:])
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

    # 创建 Agent（通过核心工厂模块，与 server / repl 共享同一组装逻辑）
    from nexus.core.factory import create_agent
    agent = create_agent(config)

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
# nexus serve / nexus ui 子命令
# ------------------------------------------------------------------


def _run_server_command(command: str, argv: list[str]) -> None:
    """``nexus serve`` / ``nexus ui`` 子命令派发器。

    - ``nexus serve [--port N] [--host H]``：启动 HTTP + WebSocket 服务
    - ``nexus ui [--port N] [--host H]``：启动服务后自动打开浏览器
    """
    parser = argparse.ArgumentParser(
        prog=f"nexus {command}",
        description="启动 Nexus Server（HTTP + WebSocket 后端）",
    )
    parser.add_argument("--port", type=int, default=8321, help="监听端口（默认 8321）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    args = parser.parse_args(argv)

    # 依赖检查：fastapi/uvicorn 为可选依赖（pip install -e ".[server]"）
    try:
        import uvicorn  # noqa: F401
        from nexus.server.app import create_app
    except ImportError:
        print(
            "缺少 server 依赖，请运行 pip install -e \".[server]\" 后重试。"
        )
        return

    app = create_app(work_dir=os.getcwd())

    if command == "ui":
        # 服务启动后自动打开浏览器（延迟 1 秒等待 uvicorn 就绪）
        import threading
        import webbrowser

        url = f"http://127.0.0.1:{args.port}/"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"Nexus Server 启动中: http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port)


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

    from nexus.core.factory import create_agent
    agent = create_agent(config)

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
