"""Agent 工厂模块 —— 封装 Agent 的完整组装流程，抽取自 cli/main.py。

设计思路
--------
将原本散落在 ``nexus/cli/main.py`` 私有函数 ``_create_llm`` / ``_register_tools``
中的 Agent 组装逻辑收敛到核心层，提供单一入口 ``create_agent(config)``。

CLI / Server / REPL 三个调用方都只调这一个入口函数，实现"加能力只改一处"，
避免 server/app.py 和 cli/repl.py 反向 import cli/main.py 的私有函数。

公开接口
--------
- ``create_llm(config)`` —— 根据 config.default_provider 创建对应 LLM 实例
- ``register_tools(agent, config)`` —— 根据 config.tools.enabled 注册内置工具
- ``create_agent(config)`` —— 组合上述两步 + Agent 构造，返回就绪的 Agent
"""

from __future__ import annotations

import os

from nexus.core.agent.agent import Agent
from nexus.llm.base import BaseLLM
from nexus.cli.config import NexusConfig
from nexus.logging import get_logger

logger = get_logger(__name__)


def create_llm(config: NexusConfig) -> BaseLLM:
    """根据 config 创建对应的 LLM 实例。

    Provider 工厂模式——根据 config.default_provider 动态选择，
    传入 config.provider_config 中的 api_key、model、base_url、
    context_window_tokens、timeout、max_retries。

    Parameters
    ----------
    config : NexusConfig
        聚合配置实例，通过 ``config.provider_config`` 获取当前 provider 的参数。

    Returns
    -------
    BaseLLM
        对应 provider 的 LLM 实例。
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
        from nexus.llm.providers.openai import OpenAILLM
        return OpenAILLM(
            api_key=pc.api_key,
            base_url=pc.base_url,
            model=pc.model or "gpt-4o-mini",
            **common_kwargs,
        )


def register_tools(agent: Agent, config: NexusConfig) -> None:
    """根据 config.tools.enabled 注册工具。

    如果 config.tools.enabled 为空列表 → 注册所有内置工具（默认行为）。
    否则仅注册 enabled 中列出的工具。

    Parameters
    ----------
    agent : Agent
        待注册工具的 Agent 实例。
    config : NexusConfig
        聚合配置实例，通过 ``config.tools.enabled`` 控制启用的工具集合。
    """
    try:
        from nexus.tools.file_tools import (
            ReadFileTool,
            WriteFileTool,
            ListDirTool,
            SearchContentTool,
        )
        from nexus.tools.shell_tool import ShellTool
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


def create_agent(config: NexusConfig) -> Agent:
    """根据 config 组装并返回一个就绪的 Agent 实例。

    组装流程：
    1. ``create_llm(config)`` 创建 LLM 实例
    2. 构造 Agent（注入 system_prompt / max_steps / stream）
    3. ``register_tools(agent, config)`` 注册内置工具

    Parameters
    ----------
    config : NexusConfig
        聚合配置实例。

    Returns
    -------
    Agent
        完成 LLM 配置和工具注册的 Agent 实例。
    """
    llm = create_llm(config)
    agent = Agent(
        llm=llm,
        system_prompt=config.system_prompt,
        max_steps=config.max_steps,
        stream=config.stream,
    )
    register_tools(agent, config)
    return agent
