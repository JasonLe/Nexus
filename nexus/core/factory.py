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
  （同步入口，不接 MCP；server create_app 等事件循环外的调用方使用）
- ``install_mcp(agent, config)`` —— 按 config.mcp_servers 安装 MCPPlugin，
  无启用项或失败时返回 None（MCP 失败不阻断 Agent 启动）
- ``create_agent_async(config)`` —— ``create_agent(config)`` + ``install_mcp``，
  CLI / Server 等 async 上下文中应优先使用此入口
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from nexus.core.agent.agent import Agent
from nexus.llm.base import BaseLLM
from nexus.cli.config import NexusConfig
from nexus.logging import get_logger

if TYPE_CHECKING:
    from nexus.tools.mcp.plugin import MCPPlugin

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

    特殊值：
    - enabled 为空列表 → 注册所有内置工具（默认行为）
    - enabled 为 ['__none__'] → 不注册任何工具（全部禁用）
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

        # 处理哨兵值
        if config.tools.enabled == ['__none__']:
            # 全部禁用，不注册任何工具
            logger.debug("All tools disabled")
            return

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


async def install_mcp(agent: Agent, config: NexusConfig) -> "MCPPlugin | None":
    """按 config.mcp_servers 为 Agent 安装 MCP 插件。

    仅在存在至少一个 enabled 的 MCP server 时安装；安装过程（含建连）
    的任何异常都只记 warning 并返回 None —— MCP server 多为外部
    进程/服务，其失败不应阻断 Agent 启动。

    Parameters
    ----------
    agent : Agent
        目标 Agent 实例。
    config : NexusConfig
        聚合配置实例，读取其 ``mcp_servers`` 字段。

    Returns
    -------
    MCPPlugin | None
        安装成功返回插件实例（可通过 ``agent.plugin_registry.get("mcp")``
        再次获取）；无启用项或安装失败返回 None。
    """
    from nexus.tools.mcp.plugin import MCPPlugin

    enabled = {
        name: cfg for name, cfg in config.mcp_servers.items() if cfg.enabled
    }
    if not enabled:
        return None

    try:
        plugin = MCPPlugin(config.mcp_servers)
        await agent.install(plugin)
        return plugin
    except Exception as exc:
        logger.warning("MCP 插件安装失败，跳过 MCP 能力: %s", exc)
        return None


async def create_agent_async(config: NexusConfig) -> Agent:
    """异步版 Agent 组装入口：``create_agent(config)`` + MCP 插件接入。

    与同步入口 ``create_agent()`` 的区别仅在于额外执行
    ``install_mcp(agent, config)`` —— 后者是 async 的（MCP 建连需要
    事件循环），无法放进同步入口。

    CLI / Server 在 async 上下文中应优先使用本入口；
    事件循环外的同步调用方（如 server create_app 的同步组装路径）
    继续使用 ``create_agent()``，MCP 接线由调用方自行处理。

    Parameters
    ----------
    config : NexusConfig
        聚合配置实例。

    Returns
    -------
    Agent
        完成 LLM 配置、工具注册与 MCP 插件安装的 Agent 实例。
    """
    agent = create_agent(config)
    await install_mcp(agent, config)
    return agent
