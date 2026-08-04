"""Nexus MCP 子包 —— Model Context Protocol 客户端集成。

设计思路
--------
Nexus Core 保持稳定，MCP（Model Context Protocol）作为可选扩展能力，
通过 ``pip install nexus-agent-runtime[mcp]`` 启用。三个层次各司其职：

- ``MCPClient`` —— 单个 MCP Server 连接封装（stdio / Streamable HTTP）；
- ``MCPToolAdapter`` —— 把远端 MCP 工具适配为本地 ``BaseTool``，
  以 ``mcp__{server}__{tool}`` 名称注册进 ToolRegistry；
- ``MCPManager`` —— 按配置批量管理连接与工具注册，提供状态查询与重连。

优雅降级
--------
未安装官方 MCP SDK（``mcp`` 包）时，本子包 **可以正常 import**，
仅在实际 ``connect()`` 时抛出带安装提示的错误。上层（CLI / Server）
可通过 ``MCP_SDK_AVAILABLE`` 标志提前判断并隐藏相关入口。
"""

from __future__ import annotations

from nexus.tools.mcp.adapter import MCPToolAdapter
from nexus.tools.mcp.client import MCP_SDK_AVAILABLE, MCPClient
from nexus.tools.mcp.manager import MCPManager

__all__ = [
    "MCP_SDK_AVAILABLE",
    "MCPClient",
    "MCPManager",
    "MCPToolAdapter",
]
