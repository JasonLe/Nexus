"""Nexus MCP Manager —— 批量管理 MCP Server 连接与工具注册。

设计思路
--------
单个 ``MCPClient`` 只关心一条连接；实际使用中往往配置多个 MCP Server，
且需要把它们的工具统一注入 ``ToolRegistry``。``MCPManager`` 承担这层编排：

- **配置驱动** —— 接收 ``dict[name, config]`` 配置集合，逐项建连、
  拉取工具列表、创建 ``MCPToolAdapter`` 并注册。
- **故障隔离** —— 单个 server 连接失败仅记 warning 并把状态标记为
  ``error``，不影响其余 server 的就绪。MCP server 多为外部进程/服务，
  部分不可用不应拖垮整个 Agent 启动流程。
- **可运维** —— 提供 ``get_status()`` / ``is_connected()`` 状态查询与
  ``reconnect()`` / ``remove()`` 单点操作，支撑 CLI ``/mcp`` 类命令。

循环依赖规避
------------
配置对象（``MCPServerConfig`` dataclass）定义在 ``nexus.cli.config``，
而 config 层将来可能反向引用 tools 层。因此本模块 **不 import config 模块**，
仅通过 ``getattr`` duck-typing 读取 ``name/command/args/env/url/enabled``
属性（也兼容 dict 形态的配置），保持依赖方向单向。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nexus.logging import get_logger
from nexus.tools.mcp.adapter import MCPToolAdapter
from nexus.tools.mcp.client import MCPClient

if TYPE_CHECKING:
    from nexus.tools.registry import ToolRegistry

logger = get_logger(__name__)


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    """duck-type 读取配置字段，同时兼容对象属性与 dict 两种形态。"""
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


class MCPManager:
    """MCP Server 连接与工具注册的管理器。

    内部状态：

    - ``_clients: dict[str, MCPClient]`` —— 已建立连接的客户端；
    - ``_configs: dict[str, Any]`` —— 最近一次 connect_all 传入的配置副本，
      供 ``reconnect()`` 重建连接使用；
    - ``_states: dict[str, dict]`` —— 每个 server 的运行时状态
      （status/error/tools 等），``get_status()`` 直接由其派生。
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._configs: dict[str, Any] = {}
        self._states: dict[str, dict[str, Any]] = {}

    async def connect_all(
        self,
        configs: dict[str, Any],
        tool_registry: ToolRegistry,
    ) -> None:
        """按配置批量连接 MCP server 并注册其工具。

        遍历所有配置：disabled 的 server 仅记录状态；enabled 的 server 逐个
        ``connect()`` → ``list_tools()`` → 为每个远端工具创建
        ``MCPToolAdapter`` 并 ``tool_registry.register()``。
        单个 server 失败仅记 warning 并标记 ``error`` 状态，继续处理其余 server。

        Parameters
        ----------
        configs : dict[str, Any]
            server 名 → 配置对象（duck-typed：需有
            ``command/args/env/url/enabled`` 属性或同名键）。
        tool_registry : ToolRegistry
            工具注册中心，远端工具以 ``mcp__`` 前缀名称注入。
        """
        for name, cfg in configs.items():
            self._configs[name] = cfg
            state = self._init_state(name, cfg)
            if not state["enabled"]:
                continue
            await self._connect_server(name, cfg, tool_registry)

    async def disconnect_all(self, tool_registry: ToolRegistry) -> None:
        """注销所有 ``mcp__`` 前缀工具并关闭全部连接。幂等。"""
        for name in list(self._states):
            await self._disconnect_server(name, tool_registry)

    async def reconnect(self, name: str, tool_registry: ToolRegistry) -> bool:
        """断开并重连单个 server（先注销旧工具再重新注册）。

        Returns
        -------
        bool
            重连成功返回 True；server 未配置或连接失败返回 False。
        """
        cfg = self._configs.get(name)
        if cfg is None:
            logger.warning("MCP reconnect: 未找到 server '%s' 的配置", name)
            return False
        await self._disconnect_server(name, tool_registry)
        return await self._connect_server(name, cfg, tool_registry)

    async def remove(self, name: str, tool_registry: ToolRegistry) -> None:
        """断开连接并从管理器移除该 server（含配置与状态）。"""
        await self._disconnect_server(name, tool_registry)
        self._states.pop(name, None)
        self._configs.pop(name, None)

    def get_status(self) -> list[dict[str, Any]]:
        """返回每个 server 的状态快照。

        Returns
        -------
        list[dict[str, Any]]
            每项包含 ``name`` / ``transport`` / ``enabled`` / ``status``
            （connected/error/disabled/connecting/disconnected）/ ``error`` /
            ``tool_count`` / ``tools``（[{name, description}]）。
        """
        return [
            {
                "name": state["name"],
                "transport": state["transport"],
                "enabled": state["enabled"],
                "status": state["status"],
                "error": state["error"],
                "tool_count": len(state["tools"]),
                "tools": list(state["tools"]),
            }
            for state in self._states.values()
        ]

    def is_connected(self, name: str) -> bool:
        """指定 server 当前是否处于已连接状态。"""
        state = self._states.get(name)
        return (
            state is not None
            and state["status"] == "connected"
            and name in self._clients
        )

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _init_state(self, name: str, cfg: Any) -> dict[str, Any]:
        """初始化（或重置）单个 server 的状态记录。"""
        enabled = bool(_cfg_get(cfg, "enabled", True))
        state: dict[str, Any] = {
            "name": name,
            "transport": "http" if _cfg_get(cfg, "url") else "stdio",
            "enabled": enabled,
            "status": "connecting" if enabled else "disabled",
            "error": None,
            "tools": [],
        }
        self._states[name] = state
        return state

    async def _connect_server(
        self,
        name: str,
        cfg: Any,
        tool_registry: ToolRegistry,
    ) -> bool:
        """连接单个 server 并注册其全部工具，失败时更新状态并返回 False。"""
        state = self._states[name]
        state["status"] = "connecting"
        state["error"] = None

        client = MCPClient(
            name=name,
            command=_cfg_get(cfg, "command"),
            args=list(_cfg_get(cfg, "args") or []),
            env=dict(_cfg_get(cfg, "env") or {}),
            url=_cfg_get(cfg, "url"),
        )
        try:
            await client.connect()
            remote_tools = await client.list_tools()
        except Exception as exc:
            state["status"] = "error"
            state["error"] = str(exc)
            await client.close()
            logger.warning("MCP server '%s' 连接失败: %s", name, exc)
            return False

        self._clients[name] = client
        for remote_tool in remote_tools:
            adapter = MCPToolAdapter(
                client=client, server_name=name, remote_tool=remote_tool
            )
            try:
                tool_registry.register(adapter)
            except ValueError as exc:
                # 同名工具已被注册（如与内置工具/其他 server 冲突），跳过该工具
                logger.warning("MCP 工具注册跳过 (%s): %s", adapter.name, exc)
                continue
            state["tools"].append(
                {"name": adapter.name, "description": adapter.description}
            )

        state["status"] = "connected"
        logger.info(
            "MCP server '%s' 就绪，注册 %d 个工具", name, len(state["tools"])
        )
        return True

    async def _disconnect_server(
        self,
        name: str,
        tool_registry: ToolRegistry,
    ) -> None:
        """注销单个 server 的全部工具并关闭连接。幂等。"""
        state = self._states.get(name)
        if state is not None:
            for tool_info in state["tools"]:
                tool_registry.unregister(tool_info["name"])
            state["tools"] = []

        client = self._clients.pop(name, None)
        if client is not None:
            await client.close()

        if state is not None and state["status"] not in ("disabled", "error"):
            state["status"] = "disconnected"
