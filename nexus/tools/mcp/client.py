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
4. **stdio stderr 取证** —— 默认的 ``stdio_client`` 把子进程 stderr 透传到
  ``sys.stderr``，但当 Nexus 以服务/桌面等方式运行、stderr 被重定向到日志
  时，子进程崩溃（如 ``ModuleNotFoundError``、缺少 API Key、版本不兼容）
  的根因只留在日志里，调用方拿到的只是 ``McpError: Connection closed``。
  本模块在 ``connect()`` 时做 **预飞检查**（pre-flight）：先短时间拉起
  子进程（默认 0.8s），若它在握手前崩溃（如 import 错误、缺依赖、
  API Key 校验失败），把 stderr 抓回来拼进异常消息，根因直达用户。
  预飞通过后再走官方 ``stdio_client`` 协议层，行为与之前完全一致。

注意事项
--------
- ``MCPClient`` 实例不是并发安全的，同一时刻应对应一个事件循环内的使用。
- stdio 传输会继承当前进程环境变量（``os.environ`` 拷贝），用户配置的 env
  覆盖同名变量，以满足 API Key 等注入需求。
- 预飞检查会额外启动一次子进程（< 1s 退出），对正常工作的 server
  只增加不到 1s 启动延迟。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import AsyncExitStack
from typing import Any

from nexus.logging import get_logger

logger = get_logger(__name__)

# 预飞检查等待时间（秒）。短到能捕捉 import 错误（< 0.5s），
# 长到不至于让正常 server 误判（典型 MCP server 启动 < 0.3s）。
_PREFLIGHT_TIMEOUT = 0.8


def _preflight_check(command: str, args: list[str], env: dict[str, str]) -> str:
    """短时间拉起 MCP server 子进程，捕获其在握手前崩溃的 stderr。

    大多数 MCP server 启动后会阻塞等待 stdin（协议消息），所以 0.8s 内
    进程未退出即视为通过预飞。如果在此期间退出且 returncode != 0，
    说明子进程在初始化阶段失败（import 错误、配置缺失、API Key 缺失等），
    把 stderr 完整返回供上层拼进异常消息。

    Parameters
    ----------
    command, args, env
        与 ``MCPClient`` 构造参数一致。env 会覆盖当前进程环境变量的副本。

    Returns
    -------
    str
        预飞期间捕获的 stderr 内容（末尾若干行）。空字符串表示预飞通过。
    """
    import sys
    import time

    merged_env = {**os.environ, **env}
    try:
        proc = subprocess.Popen(
            [command, *args],
            env=merged_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return f"无法启动子进程: {exc}"
    except OSError as exc:
        return f"启动子进程失败 ({type(exc).__name__}): {exc}"

    try:
        time.sleep(_PREFLIGHT_TIMEOUT)
    except KeyboardInterrupt:  # pragma: no cover
        proc.terminate()
        proc.wait()
        raise

    poll = proc.poll()
    if poll is None:
        # 仍在运行 —— 视为正常 server，立即终止
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        return ""

    # 已退出：捕获 stderr（stdout 通常为空）
    try:
        _, stderr_bytes = proc.communicate(timeout=2.0)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return ""
    try:
        return stderr_bytes.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover
        return ""


def _augment_error(exc: BaseException, stderr_tail: str) -> BaseException:
    """把 stderr 尾部追加到异常的展示消息中，保持原类型与堆栈。

    行为约定：
    - 异常类型不变：调用方 ``except McpError`` 等仍能命中；
    - ``str(exc)`` 与 ``args[0]`` 反映新消息（仅展示层影响）；
    - 原始异常通过 ``raise ... from exc`` 链路保留，``__cause__`` 不变。

    特殊处理 ``McpError``：其 ``__init__`` 强制要求 ``ErrorData`` 参数（不是
    str），构造时用 str 会抛 ``AttributeError``。这里直接修改 ``exc.error.
    message`` 与 ``exc.args`` 实现追加，避免重建异常对象。
    """
    head = str(exc) or type(exc).__name__
    new_msg = f"{head}\n\n[server stderr 尾部]\n{stderr_tail}"

    # McpError 路径：in-place 修改 .error.message 与 args
    error_obj = getattr(exc, "error", None)
    if error_obj is not None and hasattr(error_obj, "message"):
        try:
            error_obj.message = new_msg
        except Exception:  # pragma: no cover - ErrorData 不可写时
            return exc
        exc.args = (new_msg,) + exc.args[1:]
        return exc

    # 普通异常路径：直接重置 args 即可
    if len(exc.args) >= 2:
        new_exc = type(exc)(*exc.args[:1], new_msg, *exc.args[2:])
    else:
        new_exc = type(exc)(new_msg)
    return new_exc


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
    preflight_timeout : float
        stdio 预飞检查等待秒数，默认 0.8s。设为 0 可禁用预飞。

    使用示例
    --------

    >>> client = MCPClient(name="fs", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "."])
    >>> await client.connect()
    >>> tools = await client.list_tools()
    >>> await client.close()
    """

    DEFAULT_CONNECT_TIMEOUT: float = 30.0
    DEFAULT_PREFLIGHT_TIMEOUT: float = _PREFLIGHT_TIMEOUT

    def __init__(
        self,
        *,
        name: str = "",
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        preflight_timeout: float = DEFAULT_PREFLIGHT_TIMEOUT,
    ) -> None:
        self._name = name
        self._command = command
        self._args = list(args) if args else []
        self._env = dict(env) if env else {}
        self._url = url
        self._connect_timeout = connect_timeout
        self._preflight_timeout = preflight_timeout
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

        幂等：已连接时直接返回。连接失败时清理已分配资源后抛出原异常；
        对于 stdio 传输，会做 **预飞检查**（短时间拉起子进程，捕获
        握手前崩溃的 stderr），失败时把 stderr 拼进异常消息，让用户
        能直接看到 ``ModuleNotFoundError`` 等根因（否则只看到
        ``McpError: Connection closed``，根因信息全在子进程 stderr 中）。

        Raises
        ------
        RuntimeError
            未安装 MCP Python SDK，或预飞检查发现子进程立即崩溃。
        TimeoutError
            超过 ``connect_timeout`` 未完成连接与初始化。
        McpError
            MCP 协议层错误；stdio 模式下若预飞捕获到 stderr 尾部，错误
            消息会附加 ``\\n\\n[server stderr 尾部]\\n<lines>`` 段。
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

        # stdio 传输的预飞检查（在事件循环里跑一次阻塞 Popen.wait 是可接受
        # 的，因为 0.8s 超时很短；改用 ``asyncio.to_thread`` 不增加复杂度
        # 但需要 0.8s 的 event loop 让出，先简单同步实现）
        preflight_stderr = ""
        if not self._url and self._preflight_timeout > 0:
            try:
                preflight_stderr = await asyncio.to_thread(
                    _preflight_check,
                    self._command or "",
                    self._args,
                    self._env,
                )
            except Exception as exc:  # pragma: no cover - 防御
                logger.debug("MCP 预飞检查异常（忽略）: %s", exc)
            if preflight_stderr:
                # 子进程在握手前崩溃 —— 直接抛错并附 stderr
                hint = self._format_preflight_hint(preflight_stderr)
                raise RuntimeError(
                    f"MCP server '{self._name}' 启动失败：{hint}"
                ) from None

        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self._connect_timeout):
                if self._url:
                    streams = await stack.enter_async_context(
                        _streamable_http_client(self._url)
                    )
                    session = await stack.enter_async_context(
                        ClientSession(streams[0], streams[1])
                    )
                else:
                    merged_env = {**os.environ, **self._env}
                    params = StdioServerParameters(
                        command=self._command,
                        args=self._args,
                        env=merged_env,
                    )
                    # 走官方 stdio_client：协议层正确，能与 ClientSession
                    # 完整生命周期对齐；预飞已经过滤掉启动崩溃的情况，
                    # 这里的 Connection closed 通常意味着运行时问题（如
                    # 握手时被 server 主动关闭），不常见。
                    streams = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(
                        ClientSession(streams[0], streams[1])
                    )
                await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise

        self._exit_stack = stack
        self._session = session
        logger.info(
            "MCP server '%s' 已连接 (transport=%s)", self._name, self.transport
        )
    @staticmethod
    def _format_preflight_hint(stderr: str) -> str:
        """把预飞 stderr 截断为友好提示，保留最后若干行（典型场景：import
        错误堆栈 5~10 行足够定位）。

        同时识别常见根因（``mcp.server.fastmcp`` 不存在等 SDK 版本不兼容
        模式），在消息末尾追加可操作的修复建议，让用户无需自己去搜根因。
        """
        lines = stderr.splitlines()
        if len(lines) > 30:
            shown = "\n".join(lines[-30:])
            tail_block = f"... ({len(lines) - 30} 行省略) ...\n{shown}"
        else:
            tail_block = stderr

        hint = f"子进程在握手前退出，stderr 尾部:\n{tail_block}"

        # 常见兼容性问题：mcp 2.x 移除了 mcp.server.fastmcp。
        # uvx 拉起的临时环境默认装最新 mcp，导致用 1.x 风格写的 server
        # 包 import 失败。给一个具体的修复建议。
        if "mcp.server.fastmcp" in stderr and "ModuleNotFoundError" in stderr:
            hint += (
                "\n\n[可能原因] 该 MCP server 使用的是 mcp 1.x 风格的 "
                "``from mcp.server.fastmcp import FastMCP``，而 uvx 拉起的"
                "临时环境默认安装 mcp 2.x（已移除该模块）。\n"
                "[修复建议] 在 args 中显式锁定 mcp 1.x：\n"
                "  args:\n"
                "    - --from\n"
                "    - <package-name>\n"
                "    - --with\n"
                "    - mcp<2\n"
                "    - <package-name>\n"
                "（或自行 fork 该 server 包改用 ``mcp.server.mcpserver.MCPServer``）"
            )
        # npm 包名错误：典型场景是复制粘贴时混入了 Unicode 短横线（U+2011）
        # 等 URL 不友好字符，npm 拒绝解析。这类问题需要用户肉眼对比官方包名。
        elif "npm error" in stderr and "INVALIDPACKAGENAME" in stderr:
            hint += (
                "\n\n[可能原因] args 中的包名含有 URL 不友好字符（如 Unicode "
                "短横线 U+2011 而非普通 ASCII ``-``），npm 拒绝解析。\n"
                "[修复建议] 到 npmjs.com 复制包名（确保是普通 ASCII 字符），"
                "或改用具体版本号（如 ``@scope/pkg@1.2.3``）以避开 "
                "``@latest`` 在某些环境下的解析差异。"
            )
        return hint

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
