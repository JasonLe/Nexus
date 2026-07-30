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
from logging.handlers import RotatingFileHandler
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
    log_file: str | None = None,
) -> None:
    """配置 CLI 日志（终端 + 文件持久化）。

    终端日志级别由 verbose/debug 控制：
    - debug=True:  DEBUG 级别
    - verbose=True: INFO 级别
    - 默认: WARNING 级别

    文件日志始终输出 DEBUG 级别，存储在 ~/.nexus/logs/nexus.log，
    使用 RotatingFileHandler（10MB × 5 备份）防止磁盘膨胀。
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

    # 文件日志 —— 始终 DEBUG 级别
    if log_file is None:
        log_dir = Path.home() / ".nexus" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = str(log_dir / "nexus.log")

    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    nexus_logger.addHandler(file_handler)
    nexus_logger.setLevel(logging.DEBUG)

    logger.debug(
        "Logging configured: terminal_level=%s, file=%s",
        logging.getLevelName(level),
        log_file,
    )


def _create_llm(config: NexusConfig) -> BaseLLM:
    """根据 config 创建对应的 LLM 实例。

    Provider 工厂模式——根据 config.default_provider 动态选择，
    传入 config.provider_config 中的 api_key、model、base_url、max_tokens。
    """
    provider = config.default_provider.lower()
    pc = config.provider_config

    if provider == "minimax":
        from nexus.llm.providers.minimax import MiniMaxAnthropicLLM
        return MiniMaxAnthropicLLM(
            api_key=pc.api_key,
            model=pc.model or "MiniMax-Text-01",
            base_url=pc.base_url or "https://api.minimaxi.com/anthropic",
        )
    elif provider == "anthropic":
        from nexus.llm.providers.anthropic import AnthropicLLM
        return AnthropicLLM(
            api_key=pc.api_key,
            model=pc.model or "claude-sonnet-4-20250514",
            **({"base_url": pc.base_url} if pc.base_url else {}),
        )
    else:
        return OpenAILLM(
            api_key=pc.api_key,
            base_url=pc.base_url,
            model=pc.model or "gpt-4o-mini",
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
        all_tools = {
            "read_file": ReadFileTool(work_dir=config.work_dir or os.getcwd()),
            "write_file": WriteFileTool(work_dir=config.work_dir or os.getcwd()),
            "list_dir": ListDirTool(),
            "search_content": SearchContentTool(),
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

        display.render_divider()
        display.render_assistant_header()

        tool_count = 0

        async def on_after_llm(event) -> None:
            payload = event.payload
            response = payload.get("response")
            if response and hasattr(response, "content") and response.content:
                has_tool_calls = bool(getattr(response, "tool_calls", None))
                if has_tool_calls:
                    display.render_thinking(response.content)
                else:
                    display.render_response(response.content)
            elif payload.get("content"):
                display.render_response(payload["content"])

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
        await agent.events.subscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
        await agent.events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)

        try:
            with display.show_spinner("🤔 Nexus is working..."):
                await agent.run(prompt)
        finally:
            await agent.events.unsubscribe(EventType.AFTER_LLM_CALL, on_after_llm)
            await agent.events.unsubscribe(EventType.BEFORE_TOOL_CALL, on_before_tool)
            await agent.events.unsubscribe(EventType.AFTER_TOOL_CALL, on_after_tool)

    asyncio.run(_run())


def _run_continue(agent: Agent, config: NexusConfig) -> None:

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
        repl = Repl(agent=agent, display=display, config=config)
        await repl.restore_session(session)
        await repl.run()

    asyncio.run(_run())


def main() -> None:
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
        "--list-sessions", action="store_true", help="列出历史会话",
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

    # 日志配置
    setup_logging(verbose=args.verbose, debug=args.debug)

    # --list-sessions
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
            _run_continue(agent, config)
        elif args.prompt:
            _run_single(agent, config, args.prompt)
        else:
            from nexus.cli.repl import Repl
            from nexus.cli.display import DisplayManager

            display = DisplayManager()
            repl = Repl(agent=agent, display=display, config=config)
            asyncio.run(repl.run())
    finally:
        if args.save_config:
            try:
                saved_path = save_config(config)
                print(f"Config saved to {saved_path}")
            except Exception as e:
                logger.warning("Failed to save config: %s", e)


if __name__ == "__main__":
    main()
