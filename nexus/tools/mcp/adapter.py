"""Nexus MCP Tool Adapter —— 将远端 MCP 工具适配为本地 BaseTool。

设计思路
--------
MCP Server 暴露的工具与 Nexus 本地工具之间存在模型差异：

- 远端工具只有 name/description/inputSchema 元数据，执行需经网络/子进程调用；
  本地 ``BaseTool`` 要求 ``execute(args) -> ToolResult`` 的同步世界观。
- ``MCPToolAdapter`` 作为 **防腐层（Anti-Corruption Layer）**，把远端工具包装成
  标准 ``BaseTool`` 注册进 ``ToolRegistry``，Agent/ToolExecutor 无需感知 MCP 存在。

命名规范
--------
本地工具名 = ``mcp__{server_name}__{remote_tool_name}``，其中 server 与 tool 名中的
非字母数字字符（如 ``-``）统一替换为 ``_``。原因：

1. 避免与内置工具命名冲突（``mcp__`` 前缀天然隔离命名空间）；
2. 部分 LLM 的 function calling 对工具名有 ``^[a-zA-Z0-9_]+$`` 约束；
3. 名称自解释，日志中可直接追溯来源 server。

错误处理
--------
``execute`` 绝不抛出未捕获异常：远端调用失败（网络错误、远端报错
``isError=True``）统一转为 ``ToolResult.fail(error=...)``，交给 LLM 阅读后
自行纠错，符合 BaseTool 扩展约定。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from nexus.logging import get_logger
from nexus.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from nexus.tools.mcp.client import MCPClient

logger = get_logger(__name__)

_NON_ALNUM_RE = re.compile(r"[^0-9a-zA-Z_]")

_FALLBACK_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _sanitize(name: str) -> str:
    """将名称中的非字母数字字符替换为 ``_``，满足工具命名约束。"""
    return _NON_ALNUM_RE.sub("_", name)


def _convert_result(result: Any) -> tuple[Any, bool]:
    """把 SDK 返回的 CallToolResult 转换为 ``(data, is_error)``。

    转换规则：

    - 有 ``structuredContent`` 时优先返回结构化数据；
    - 否则拼接所有 TextContent 块的文本；
    - 其余内容块（图片/资源等）以 dict 形式列入结果列表；
    - 空结果返回空字符串。

    Parameters
    ----------
    result : Any
        ``MCPClient.call_tool`` 返回的 SDK 原始结果对象。

    Returns
    -------
    tuple[Any, bool]
        ``(数据或错误描述, 是否为错误结果)``。
    """
    is_error = bool(
        getattr(result, "is_error", False) or getattr(result, "isError", False)
    )

    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)

    texts: list[str] = []
    others: list[Any] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
        elif hasattr(block, "model_dump"):
            others.append(block.model_dump())
        else:
            others.append(str(block))

    if structured is not None:
        data: Any = structured
    elif others:
        data = {"text": "\n".join(texts), "content": others}
    else:
        data = "\n".join(texts)
    return data, is_error


class MCPToolAdapter(BaseTool):
    """将单个远端 MCP 工具适配为 Nexus 本地工具。

    Parameters
    ----------
    client : MCPClient
        已连接的 MCP 客户端，工具的 execute 经其转发到远端 server。
    server_name : str
        远端 server 名称，用于本地工具名与 description 来源标识。
    remote_tool : dict[str, Any]
        远端工具元数据，含 ``name`` / ``description`` / ``schema``（inputSchema）
        三个键，通常来自 ``MCPClient.list_tools()``。
    """

    def __init__(
        self,
        client: MCPClient,
        server_name: str,
        remote_tool: dict[str, Any],
    ) -> None:
        self._client = client
        self._server_name = server_name
        self._remote_name: str = str(remote_tool.get("name", ""))
        self._remote_description: str = str(remote_tool.get("description", "") or "")
        self._remote_schema: Any = remote_tool.get("schema")

    @property
    def name(self) -> str:
        """本地工具名：``mcp__{server}__{tool}``，非法字符替换为 ``_``。"""
        return f"mcp__{_sanitize(self._server_name)}__{_sanitize(self._remote_name)}"

    @property
    def description(self) -> str:
        """透传远端描述，附加 ``[MCP: server]`` 前缀标识来源。"""
        if self._remote_description:
            return f"[MCP: {self._server_name}] {self._remote_description}"
        return f"[MCP: {self._server_name}] {self._remote_name}"

    @property
    def schema(self) -> dict[str, Any]:
        """透传远端 inputSchema；非合法 object schema 时回退为空 object schema。"""
        schema = self._remote_schema
        if isinstance(schema, dict) and schema.get("type", "object") == "object":
            return schema
        return dict(_FALLBACK_SCHEMA)

    @property
    def timeout(self) -> float | None:
        """MCP 远端调用可能较慢，放宽到 120s。"""
        return 120.0

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """转发调用到远端 MCP server，并把结果转换为 ToolResult。

        绝不抛出未捕获异常：远端错误（isError）与本地异常（连接断开、
        序列化失败等）均返回 ``ToolResult.fail``。
        """
        try:
            result = await self._client.call_tool(self._remote_name, args)
            data, is_error = _convert_result(result)
        except Exception as exc:
            logger.warning(
                "MCP 工具调用异常 (tool=%s): %s", self.name, exc,
                extra={"tool_name": self.name},
            )
            return ToolResult.fail(
                error=f"MCP 工具调用失败 ({self._server_name}/{self._remote_name}): {exc}",
                tool_name=self.name,
            )

        if is_error:
            error_text = data if isinstance(data, str) else str(data)
            return ToolResult.fail(
                error=f"远端 MCP 工具返回错误: {error_text}",
                tool_name=self.name,
            )
        return ToolResult.ok(data=data, tool_name=self.name)
