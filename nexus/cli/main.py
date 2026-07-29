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
from typing import Any

from nexus.core.agent.agent import Agent
from nexus.llm.providers.openai import OpenAILLM
from nexus.cli.config import CLIConfig, load_config
from nexus.logging import NexusFormatter, get_logger

logger = get_logger(__name__)


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    """配置 CLI 日志。

    根据 verbose/debug 标志设置 nexus logger 的日志级别和输出格式。
    - debug=True:  DEBUG 级别，所有内部日志
    - verbose=True: INFO 级别，关键操作日志
    - 默认: WARNING 级别，仅错误和警告

    使用 NexusFormatter 将 extra 字段（run_id、step 等）展平输出。

    Parameters
    ----------
    verbose : bool
        是否显示详细日志（INFO 级别）。
    debug : bool
        是否显示调试日志（DEBUG 级别），优先级高于 verbose。
    """
    level = logging.DEBUG if debug else (logging.INFO if verbose else logging.WARNING)

    # 避免重复添加 handler
    nexus_logger = logging.getLogger("nexus")
    # 清除现有 handler 以便重新配置级别
    nexus_logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(NexusFormatter())
    nexus_logger.addHandler(handler)
    nexus_logger.setLevel(level)

    logger.debug(
        "Logging configured: level=%s, debug=%s, verbose=%s",
        logging.getLevelName(level),
        debug,
        verbose,
    )


def _run_single(agent: Agent, config: CLIConfig, prompt: str) -> None:
    """直接执行模式：运行一次任务后退出。

    适用于 ``nexus "prompt"`` 的一次性问答场景。
    创建 EventBus 订阅者，流式展示 Agent 的思考和工具调用过程，
    任务完成后打印最终结果并退出。

    Parameters
    ----------
    agent : Agent
        已配置好 LLM 和工具的 Agent 实例。
    config : CLIConfig
        合并后的 CLI 配置。
    prompt : str
        用户输入的任务描述。
    """

    async def _run() -> None:
        # 延迟导入 DisplayManager，该模块可能在后续任务中创建
        try:
            from nexus.cli.display import DisplayManager
        except ImportError:
            logger.warning("DisplayManager not available, running without display")
            state = await agent.run(prompt)
            # 输出最终结果
            if state.messages:
                last_msg = state.messages[-1]
                if last_msg.get("role") == "assistant":
                    print(last_msg.get("content", ""))
            return

        display = DisplayManager()

        # 通过 EventBus 订阅事件，流式展示 Agent 的思考过程
        # BEFORE_LLM_CALL: 显示 "Thinking..." 提示
        # AFTER_LLM_CALL: 展示 LLM 响应内容
        # BEFORE_TOOL_CALL: 展示即将调用的工具
        # AFTER_TOOL_CALL: 展示工具执行结果
        from nexus.core.event.event_types import EventType

        async def on_before_llm(event) -> None:
            display.show_spinner("Thinking...")

        async def on_after_llm(event) -> None:
            payload = event.payload
            content = payload.get("content", "")
            if content:
                display.render_streaming_content(content)

        async def on_before_tool(event) -> None:
            payload = event.payload
            tool_name = payload.get("tool_name", "unknown")
            args = payload.get("args", {})
            display.show_info(f"🔧 Calling {tool_name}({_format_args(args)})")

        async def on_after_tool(event) -> None:
            payload = event.payload
            tool_name = payload.get("tool_name", "unknown")
            result = payload.get("result", "")
            success = payload.get("success", True)
            display.render_tool_call(tool_name, {}, str(result)[:200], success=success)

        # 注册事件处理器
        await agent.events.subscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
        await agent.events.subscribe(EventType.AFTER_LLM_CALL, on_after_llm)
        await agent.events.subscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
        await agent.events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)

        try:
            state = await agent.run(prompt)
            # 输出最终结果
            if state.messages:
                last_msg = state.messages[-1]
                if last_msg.get("role") == "assistant":
                    display.show_info("")
                    display.render_streaming_content(last_msg.get("content", ""))
        finally:
            # 清理事件处理器，防止泄漏
            await agent.events.unsubscribe(EventType.BEFORE_LLM_CALL, on_before_llm)
            await agent.events.unsubscribe(EventType.AFTER_LLM_CALL, on_after_llm)
            await agent.events.unsubscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
            await agent.events.unsubscribe(EventType.AFTER_TOOL_CALL, on_after_tool)

    asyncio.run(_run())


def _format_args(args: dict) -> str:
    """格式化工具参数字典为简短的字符串表示。

    用于事件处理器中的日志和展示，将参数截断到合理长度。
    """
    if not args:
        return ""
    items = [f"{k}={str(v)[:50]}" for k, v in args.items()]
    return ", ".join(items)
    """恢复会话模式：加载最近一次会话继续对话。

    从 SessionManager 中读取最近会话的完整状态，
    恢复到 Agent 上下文中，然后进入 REPL 交互模式。

    Parameters
    ----------
    agent : Agent
        已配置好 LLM 和工具的 Agent 实例。
    config : CLIConfig
        合并后的 CLI 配置。
    """

    async def _run() -> None:
        try:
            from nexus.cli.session import SessionManager
        except ImportError:
            print("Session manager is not available yet.")
            return

        mgr = SessionManager()
        session = mgr.load_latest()
        if session is None:
            print("No previous session found to continue.")
            return

        print(f"Continuing session: {session.get('summary', 'Unknown')}")

        try:
            from nexus.cli.repl import Repl
            from nexus.cli.display import DisplayManager
        except ImportError:
            print("REPL is not available yet.")
            return

        display = DisplayManager()
        repl = Repl(agent=agent, display=display, config=config)
        # 将会话历史恢复到 agent 上下文中
        await repl.restore_session(session)
        await repl.run()

    asyncio.run(_run())


def main() -> None:
    """Nexus CLI 主入口 —— 解析参数、加载配置、派发命令。

    命令派发逻辑：
    1. ``--list-sessions`` → 列出所有历史会话（无需创建 Agent）
    2. ``--continue`` → 恢复最近一次会话
    3. ``nexus "prompt"`` → 单次任务执行
    4. ``nexus``（无参数）→ 进入 REPL 交互模式
    """
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus CLI Agent - 命令行编程助理",
    )
    parser.add_argument(
        "prompt", nargs="?", help="直接执行的任务描述"
    )
    parser.add_argument(
        "--model", help="LLM 模型名称 (默认: gpt-4o-mini)"
    )
    parser.add_argument(
        "--api-key", help="API Key (默认从环境变量 NEXUS_API_KEY 读取)"
    )
    parser.add_argument(
        "--base-url", help="API Base URL"
    )
    parser.add_argument(
        "--system-prompt", help="系统提示词"
    )
    parser.add_argument(
        "--max-steps", type=int, help="最大执行步数 (默认: 30)"
    )
    parser.add_argument(
        "--work-dir", help="工作目录 (默认: 当前目录)"
    )
    parser.add_argument(
        "--continue",
        dest="continue_session",
        action="store_true",
        help="恢复最近会话",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="列出历史会话",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="显示详细日志"
    )
    parser.add_argument(
        "--debug", action="store_true", help="显示调试日志"
    )

    args = parser.parse_args()

    # 日志配置（必须在任何 logger 使用前调用）
    setup_logging(verbose=args.verbose, debug=args.debug)

    # --list-sessions 不需要创建 Agent，直接处理
    if args.list_sessions:
        try:
            from nexus.cli.session import SessionManager
        except ImportError:
            print("Session manager is not available yet.")
            return

        mgr = SessionManager()
        sessions = mgr.list_sessions()
        if not sessions:
            print("No saved sessions.")
        else:
            for s in sessions:
                print(f"  {s['id']}  {s['timestamp']}  {s['summary']}")
        return

    # 加载配置（三级合并：默认值 → 配置文件 → 环境变量 → 命令行参数）
    work_dir = args.work_dir or os.getcwd()
    # 提取用户显式传入的非 None 命令行参数
    cli_args = {k: v for k, v in vars(args).items() if v is not None}
    config = load_config(cli_args, work_dir=work_dir)

    # 创建 LLM Provider
    llm = OpenAILLM(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )

    # 创建 Agent
    agent = Agent(
        llm=llm,
        system_prompt=config.system_prompt,
        max_steps=config.max_steps,
    )

    # 注册 CLI 内置文件工具
    try:
        from nexus.cli.tools.file_tools import register_all_tools
        register_all_tools(agent)
    except ImportError:
        logger.debug("File tools not available, skipping registration")

    # 根据模式派发
    if args.continue_session:
        _run_continue(agent, config)
    elif args.prompt:
        _run_single(agent, config, args.prompt)
    else:
        # 无参数 → 进入 REPL 交互模式
        try:
            from nexus.cli.repl import Repl
            from nexus.cli.display import DisplayManager
        except ImportError:
            print("REPL mode is not available yet. Try: nexus --help")
            return

        display = DisplayManager()
        repl = Repl(agent=agent, display=display, config=config)
        asyncio.run(repl.run())


if __name__ == "__main__":
    main()
