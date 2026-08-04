"""Nexus MCP Plugin —— 以插件形态把 MCP 能力接入 Agent。

设计思路（对应 roadmap M1.1）
------------------------------
项目理念是 Runtime 零修改，一切能力通过 Plugin 扩展点接入。MCP
（Model Context Protocol）也不例外：本插件把 ``MCPManager`` 包装为
标准 ``Plugin``，复用 Agent 既有的三段式生命周期：

- **install(agent)** —— 声明式阶段。仅保存 agent 与 tool_registry 引用，
  不建立任何外部连接（符合 Plugin 基类对 install 阶段的约束）。
- **activate()** —— 连接阶段。由 ``Agent.install()`` →
  ``PluginRegistry.register()`` 自动触发，此时才调用
  ``MCPManager.connect_all()`` 批量连接 MCP server 并把远端工具
  （``mcp__`` 前缀）注入 ToolRegistry。未安装官方 MCP SDK 时优雅降级：
  记 warning 提示 ``pip install -e ".[mcp]"`` 后直接返回，插件仍可注册。
- **deactivate()** —— 断开阶段。注销全部 ``mcp__`` 工具并关闭连接，
  幂等，可安全重复调用。

运维入口
--------
``manager`` 为公开属性，REPL ``/mcp`` 命令与 Server API 通过
``agent.plugin_registry.get("mcp").manager`` 访问状态查询
（``get_status()`` / ``is_connected()``）与单点操作
（``reconnect()`` / ``remove()``）。``reload()`` 支撑配置热更新：
全量断开后按新配置重连。

循环依赖规避
------------
配置对象（``MCPServerConfig``）定义在 ``nexus.cli.config``，本模块不
import config 模块，构造参数按 ``dict[str, Any]`` duck-type 接收，
逐层传递给同样 duck-typed 的 ``MCPManager``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nexus.logging import get_logger
from nexus.plugins.base import Plugin
from nexus.tools.mcp.client import MCP_SDK_AVAILABLE
from nexus.tools.mcp.manager import MCPManager

if TYPE_CHECKING:
    from nexus.core.agent import Agent
    from nexus.tools.registry import ToolRegistry

logger = get_logger(__name__)


class MCPPlugin(Plugin):
    """MCP 插件 —— 把配置的 MCP server 工具接入 Agent。

    Parameters
    ----------
    configs : dict[str, Any]
        server 名 → 配置对象（duck-typed：需有
        ``command/args/env/url/enabled`` 属性或同名键，
        通常为 ``dict[str, MCPServerConfig]``）。

    Attributes
    ----------
    manager : MCPManager
        连接与工具注册管理器，公开供 CLI ``/mcp`` 命令与 Server API
        查询状态、触发重连。
    """

    def __init__(self, configs: dict[str, Any]) -> None:
        self.manager: MCPManager = MCPManager()
        self._configs: dict[str, Any] = dict(configs)
        self._agent: Agent | None = None
        self._tool_registry: ToolRegistry | None = None

    @property
    def name(self) -> str:
        """插件唯一名称。"""
        return "mcp"

    @property
    def version(self) -> str:
        """插件版本号（SemVer）。"""
        return "1.0.0"

    async def install(self, agent: "Agent") -> None:
        """声明式安装：保存 agent 与 tool_registry 引用，不建立连接。"""
        self._agent = agent
        self._tool_registry = agent.tool_registry

    async def activate(self) -> None:
        """激活：批量连接配置的 MCP server 并注册其工具。

        未安装官方 MCP SDK 时记 warning 并直接返回（优雅降级，
        插件仍保留在注册中心，状态可通过 manager.get_status() 查询）。
        """
        if not MCP_SDK_AVAILABLE:
            logger.warning(
                "MCP SDK 未安装，跳过 MCP server 连接；"
                '请执行 pip install -e ".[mcp]" 启用 MCP 能力'
            )
            return
        if self._tool_registry is None:
            logger.warning("MCPPlugin.activate 在 install 之前被调用，跳过连接")
            return
        await self.manager.connect_all(self._configs, self._tool_registry)

    async def deactivate(self) -> None:
        """停用：注销全部 mcp__ 工具并关闭所有连接。幂等。"""
        if self._tool_registry is None:
            return
        await self.manager.disconnect_all(self._tool_registry)

    async def reload(self, configs: dict[str, Any]) -> None:
        """按新配置全量重载：先断开所有连接，再按新配置重新连接。

        供配置热更新场景使用（如 Server API 修改 mcp_servers 后调用）。

        Parameters
        ----------
        configs : dict[str, Any]
            新的 server 配置集合，语义同构造函数参数。
        """
        self._configs = dict(configs)
        if self._tool_registry is None:
            logger.warning("MCPPlugin.reload 在 install 之前被调用，仅更新配置")
            return
        await self.manager.disconnect_all(self._tool_registry)
        if not MCP_SDK_AVAILABLE:
            logger.warning(
                "MCP SDK 未安装，跳过 MCP server 重连；"
                '请执行 pip install -e ".[mcp]" 启用 MCP 能力'
            )
            return
        await self.manager.connect_all(self._configs, self._tool_registry)
