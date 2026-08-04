"""Nexus MCP Client —— 单个 MCP Server 连接的封装。

设计思路
--------
MCP（Model Context Protocol）是 Anthropic 主导的工具/资源开放协议，
官方 Python SDK（``pip install mcp``）提供 ``ClientSession`` 与两种客户端传输：

- **stdio** —— 以子进程方式启动本地 MCP Server（command + args + env），
  通过标准输入输出通信。适合本地工具服务（文件系统、git 等）。
- **Streamable HTTP** —— 连接远端 MCP Server 的 HTTP endpoint（url）。
  适合集中部署的共享服务。

本模块在 SDK 之上做一层薄封装，原因是：

1. **生命周期集中管理** —— SDK 的 transport 与 session 均为 async context
   manager，嵌套层级深。这里用 ``contextlib.AsyncExitStack`` 统一 enter/close，
   调用方只需 ``connect()`` / ``close()`` 两个方法。
2. **优雅降级** —— MCP 属于可选能力（``pip install nexus-agent-runtime[mcp]``）。
   未安装 SDK 时模块可正常 import，仅在 ``connect()`` 时抛出带安装提示的错误，
   避免拖垮未启用 MCP 的部署。
3. **SDK 版本兼容** —— mcp 1.x 与 2.x 的 HTTP transport 函数命名不同
   （``streamablehttp_client`` vs ``streamable_http_client``），import 阶段做
   兼容探测，对上层透明。

注意事项
--------
- ``MCPClient`` 实例不是并发安全的，同一时刻应对应一个事件循环内的使用。
- stdio 传输会继承当前进程环境变量（``os.environ`` 拷贝），用户配置的 env
  覆盖同名变量，以满足 API Key 等注入需求。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any

from nexus.logging import get_logger

logger = get_logger(__name__)

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    try:
        # mcp >= 2.0：snake_case 命名
        from mcp.client.streamable_http import (
            streamable_http_client as _streamable_http_client,
        )
    except ImportError:
        # mcp 1.x：历史命名
        from mcp.client.streamable_http import (
            streamablehttp_client as _streamable_http_client,
        )

    MCP_SDK_AVAILABLE = True
    _MCP_IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover - 取决于部署环境
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    _streamable_http_client = None  # type: ignore[assignment]
    MCP_SDK_AVAILABLE = False
    _MCP_IMPORT_ERROR = str(exc)


class MCPClient:
    """单个 MCP Server 的连接封装。

    根据构造参数自动选择传输方式：提供 ``url`` 时使用 Streamable HTTP，
    否则使用 stdio（``command`` + ``args`` + ``env``）。

    Parameters
    ----------
    name : str
        Server 名称，仅用于日志与错误信息定位。
    command : str | None
        stdio 传输的启动命令（如 ``"npx"``）。
    args : list[str] | None
        stdio 传输的命令参数。
    env : dict[str, str] | None
        stdio 传输的附加环境变量，覆盖在 ``os.environ`` 拷贝之上。
    url : str | None
        Streamable HTTP 传输的服务端 URL。提供后忽略 command/args/env。
    connect_timeout : float
        连接（含握手初始化）超时秒数，默认 30s。

    使用示例
    --------

    >>> client = MCPClient(name="fs", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "."])
    >>> await client.connect()
    >>> tools = await client.list_tools()
    >>> await client.close()
    """

    DEFAULT_CONNECT_TIMEOUT: float = 30.0

    def __init__(
        self,
        *,
        name: str = "",
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self._name = name
        self._command = command
        self._args = list(args) if args else []
        self._env = dict(env) if env else {}
        self._url = url
        self._connect_timeout = connect_timeout
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any = None

    @property
    def transport(self) -> str:
        """当前传输方式：``"http"`` 或 ``"stdio"``。"""
        return "http" if self._url else "stdio"

    @property
    def connected(self) -> bool:
        """是否已建立会话。"""
        return self._session is not None

    async def connect(self) -> None:
        """建立连接并完成 MCP 握手初始化。

        幂等：已连接时直接返回。连接失败时清理已分配资源后抛出原异常。

        Raises
        ------
        RuntimeError
            未安装 MCP Python SDK。
        TimeoutError
            超过 ``connect_timeout`` 未完成连接与初始化。
        """
        if not MCP_SDK_AVAILABLE:
            raise RuntimeError(
                f"MCP server '{self._name}' 连接失败：未安装 MCP Python SDK "
                f"(import 错误: {_MCP_IMPORT_ERROR})。"
                '请运行 pip install "nexus-agent-runtime[mcp]" '
                '或 pip install "mcp>=1.10.0" 后重试。'
            )
        if self._session is not None:
            return

        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self._connect_timeout):
                if self._url:
                    streams = await stack.enter_async_context(
                        _streamable_http_client(self._url)
                    )
                else:
                    merged_env = {**os.environ, **self._env}
                    params = StdioServerParameters(
                        command=self._command,
                        args=self._args,
                        env=merged_env,
                    )
                    streams = await stack.enter_async_context(stdio_client(params))

                # stdio 与 http transport 均产出 (read_stream, write_stream) 元组
                # （mcp 1.x 的 http transport 会追加第三个元素，故用索引取值）
                session = await stack.enter_async_context(
                    ClientSession(streams[0], streams[1])
                )
                await session.initialize()
        except Exception:
            await stack.aclose()
            raise

        self._exit_stack = stack
        self._session = session
        logger.info(
            "MCP server '%s' 已连接 (transport=%s)", self._name, self.transport
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        """获取远端工具列表。

        Returns
        -------
        list[dict[str, Any]]
            每个元素为 ``{"name", "description", "schema"}``，
            其中 ``schema`` 为远端工具的 JSON Schema（inputSchema）。
        """
        session = self._require_session()
        result = await session.list_tools()
        tools: list[dict[str, Any]] = []
        for tool in result.tools:
            # mcp 2.x 属性名为 input_schema，1.x 为 inputSchema，做兼容读取
            schema = getattr(tool, "input_schema", None)
            if schema is None:
                schema = getattr(tool, "inputSchema", None)
            tools.append(
                {
                    "name": tool.name,
                    "description": getattr(tool, "description", None) or "",
                    "schema": schema if isinstance(schema, dict) else {},
                }
            )
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """调用远端工具。

        Parameters
        ----------
        name : str
            远端工具名称（不含 ``mcp__`` 前缀）。
        arguments : dict[str, Any] | None
            工具参数。

        Returns
        -------
        Any
            SDK 原始 ``CallToolResult``，内含 content blocks（文本/图片/资源）
            与 structuredContent。内容块到文本/结构化数据的转换由
            ``MCPToolAdapter`` 负责，本层保持薄封装不做解释。
        """
        session = self._require_session()
        return await session.call_tool(name, arguments or {})

    async def close(self) -> None:
        """关闭连接并释放子进程/HTTP 会话等资源。幂等。"""
        stack, self._exit_stack = self._exit_stack, None
        self._session = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as exc:
                logger.warning(
                    "MCP server '%s' 关闭连接时出现异常: %s", self._name, exc
                )
            else:
                logger.info("MCP server '%s' 已断开", self._name)

    def _require_session(self) -> Any:
        """返回已建立的会话，未连接时抛出明确错误。"""
        if self._session is None:
            raise RuntimeError(
                f"MCP server '{self._name}' 尚未连接，请先调用 connect()。"
            )
        return self._session
