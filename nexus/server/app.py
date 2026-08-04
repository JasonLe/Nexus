"""Nexus Server 应用工厂 —— FastAPI 应用组装（REST API + WebSocket 聊天）。

设计思路
--------
- **适配层定位**：只做协议适配，复用 nexus.cli.config / nexus.cli.main /
  nexus.cli.session 的现有能力，不触碰 nexus.core 决策逻辑。
- **应用工厂**：``create_app()`` 返回 FastAPI 实例，支持注入
  config / llm / agent / session_manager，便于测试隔离。
- **状态挂载**：配置、Agent、SessionManager 统一挂在 ``app.state`` 上，
  路由处理函数通过 ``request.app.state`` 访问。
- **静态托管**：若项目根存在 ``desktop/dist``，挂载到 ``/``（html=True），
  供桌面 UI 构建产物直接由本服务托管。

API 契约（前端按此开发，字段命名不可随意变更）
---------------------------------------------
REST（前缀 /api）：
- GET    /api/config           当前配置（api_key 脱敏）
- PUT    /api/config           合并更新配置并持久化（api_key 传掩码/空 = 不修改）
- GET    /api/sessions         会话列表
- GET    /api/sessions/{id}    会话详情（元信息 + messages）
- DELETE /api/sessions/{id}    删除会话（同步删日志）
- GET    /api/tools            已注册工具列表（内置 origin=builtin + MCP origin=mcp）
- GET    /api/health           健康检查
- GET    /api/mcp              MCP server 列表（配置 + 运行时状态合并）
- POST   /api/mcp              新增 MCP server（校验 + 持久化 + 热连接）
- PUT    /api/mcp/{name}       更新 MCP server（enabled/command/args/env/url 子集）
- DELETE /api/mcp/{name}       移除 MCP server（断开 + 注销工具 + 删配置）
- POST   /api/mcp/{name}/reconnect  重连刷新工具列表

WebSocket /ws/chat：
- 客户端 → 服务端：{"type": "message"|"reset"|"restore", ...}
- 服务端 → 客户端：thinking_delta / content_delta / tool_call / usage /
  done / error / reset_ok / restore_ok
- 每个连接创建时生成 8 位 session_id，每次 run 成功后覆盖式持久化到
  SessionManager（metadata.mode="desktop"），done 事件携带 session_id。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from nexus.cli.config import NexusConfig, load_config, save_config
from nexus.cli.session import SessionManager
from nexus.core.agent.agent import Agent
from nexus.core.event.event_types import EventType
from nexus.core.event.types import Event
from nexus.core.state.types import AgentState
from nexus.llm.base import BaseLLM
from nexus.logging import get_logger

logger = get_logger(__name__)

# 工具调用结果推送给前端时的截断长度
_TOOL_RESULT_MAX_LEN: int = 500
# api_key 掩码保留的尾部字符数
_API_KEY_MASK_TAIL: int = 4
# 会话 ID 前缀长度（uuid4 截断，与 SessionManager 生成的 ID 保持一致）
_SESSION_ID_LENGTH: int = 8


# ------------------------------------------------------------------
# 配置序列化（脱敏）
# ------------------------------------------------------------------


def _mask_api_key(api_key: str | None) -> tuple[bool, str | None]:
    """将 api_key 转换为 (has_api_key, 掩码) 形式，不暴露完整明文。

    掩码规则：保留前 3 位与后 4 位，中间以 **** 代替，如 ``sk-****abcd``；
    过短的 key 直接返回 ``****``。
    """
    if not api_key:
        return False, None
    if len(api_key) <= _API_KEY_MASK_TAIL + 3:
        return True, "****"
    return True, f"{api_key[:3]}****{api_key[-_API_KEY_MASK_TAIL:]}"


def _mask_env_value(value: str | None) -> str:
    """对 MCP env 值应用与 api_key 一致的前3后4掩码，避免 token 类 env 泄露。

    空值返回空字符串；短值返回 ``****``（与 ``_mask_api_key`` 规则一致）。
    """
    _, masked = _mask_api_key(value)
    return masked or ""


def _mask_env(env: dict[str, str]) -> dict[str, str]:
    """对 MCP env 字典的每个值应用掩码。"""
    return {k: _mask_env_value(v) for k, v in env.items()}


def _config_to_json(config: NexusConfig) -> dict[str, Any]:
    """将 NexusConfig 序列化为 API 契约 JSON（api_key 脱敏）。"""
    providers: dict[str, Any] = {}
    for name, pc in config.providers.items():
        has_key, masked = _mask_api_key(pc.api_key)
        providers[name] = {
            "api_key": masked,
            "has_api_key": has_key,
            "model": pc.model,
            "max_tokens": pc.max_tokens,
            "context_window_tokens": pc.context_window_tokens,
            "base_url": pc.base_url,
        }
    mcp_servers: dict[str, Any] = {}
    for name, sc in config.mcp_servers.items():
        mcp_servers[name] = {
            "command": sc.command,
            "args": list(sc.args),
            # env 脱敏：掩码回传可经 _merge_config_json 原样合并而不覆盖明文
            "env": _mask_env(sc.env),
            "url": sc.url,
            "enabled": sc.enabled,
            "transport": sc.transport,
        }
    return {
        "providers": providers,
        "default_provider": config.default_provider,
        "agent": {
            "system_prompt": config.agent.system_prompt,
            "max_steps": config.agent.max_steps,
        },
        "tools": {"enabled": list(config.tools.enabled)},
        "mcp_servers": mcp_servers,
        "stream": config.stream,
    }


def _merge_config_json(config: NexusConfig, data: dict[str, Any]) -> None:
    """将 PUT /api/config 的请求体合并进 NexusConfig（原地修改）。

    api_key 合并规则：传空值 / None / 与当前掩码一致 → 不修改；
    传其它新值 → 更新。
    """
    if not isinstance(data, dict):
        return

    # providers
    providers = data.get("providers")
    if isinstance(providers, dict):
        for name, prov in providers.items():
            if not isinstance(prov, dict):
                continue
            pc = config.providers.get(name)
            if pc is None:
                # 新增 provider：走完整字段填充
                from nexus.cli.config import ProviderConfig

                pc = ProviderConfig()
                config.providers[name] = pc
            new_key = prov.get("api_key")
            if isinstance(new_key, str) and new_key:
                _, current_mask = _mask_api_key(pc.api_key)
                # 掩码回传表示「不修改」
                if new_key != current_mask:
                    pc.api_key = new_key
            if "model" in prov and prov["model"] is not None:
                pc.model = str(prov["model"])
            if "max_tokens" in prov and prov["max_tokens"] is not None:
                pc.max_tokens = int(prov["max_tokens"])
            if "context_window_tokens" in prov and prov["context_window_tokens"] is not None:
                pc.context_window_tokens = int(prov["context_window_tokens"])
            if "base_url" in prov:
                pc.base_url = prov["base_url"]

    # default_provider
    if data.get("default_provider"):
        config.default_provider = str(data["default_provider"])

    # agent
    agent = data.get("agent")
    if isinstance(agent, dict):
        if agent.get("system_prompt") is not None:
            config.agent.system_prompt = str(agent["system_prompt"])
        if agent.get("max_steps") is not None:
            config.agent.max_steps = int(agent["max_steps"])

    # tools
    tools = data.get("tools")
    if isinstance(tools, dict) and isinstance(tools.get("enabled"), list):
        config.tools.enabled = [str(t) for t in tools["enabled"]]

    # mcp_servers —— 逐 server 合并：已存在逐字段更新，不存在新增；非法项跳过
    mcp_servers = data.get("mcp_servers")
    if isinstance(mcp_servers, dict):
        from nexus.cli.config import MCPServerConfig

        for name, srv in mcp_servers.items():
            if not isinstance(srv, dict):
                continue
            name = str(name)
            existing = config.mcp_servers.get(name)
            if existing is None:
                # 新增 server：command / url 至少一个，否则视为非法项跳过
                command = srv.get("command")
                url = srv.get("url")
                if not command and not url:
                    continue
                config.mcp_servers[name] = MCPServerConfig(
                    command=str(command) if command else None,
                    args=[str(a) for a in (srv.get("args") or [])],
                    env={str(k): str(v) for k, v in (srv.get("env") or {}).items()},
                    url=str(url) if url else None,
                    enabled=bool(srv.get("enabled", True)),
                )
            else:
                # 已存在：逐字段更新（env 掩码回传或空值不修改对应 key）
                if "command" in srv:
                    existing.command = str(srv["command"]) if srv["command"] else None
                if "args" in srv:
                    existing.args = [str(a) for a in (srv["args"] or [])]
                if "url" in srv:
                    existing.url = str(srv["url"]) if srv["url"] else None
                if "enabled" in srv and srv["enabled"] is not None:
                    existing.enabled = bool(srv["enabled"])
                env_updates = srv.get("env")
                if isinstance(env_updates, dict):
                    _merge_server_env(existing, env_updates)

    # stream
    if data.get("stream") is not None:
        config.stream = bool(data["stream"])


# ------------------------------------------------------------------
# MCP 接线与状态
# ------------------------------------------------------------------


async def _get_mcp_plugin(app: FastAPI) -> "MCPPlugin | None":
    """获取当前 agent 的 MCP 插件；未安装时按配置安装并返回。

    无 enabled server 时 ``install_mcp`` 返回 None（no-op）；
    安装失败同样返回 None —— MCP server 多为外部进程/服务，
    其失败不应阻断 Agent/Server 启动。
    """
    plugin = app.state.agent.plugin_registry.get("mcp")
    if plugin is None:
        from nexus.core.factory import install_mcp

        plugin = await install_mcp(app.state.agent, app.state.config)
    return plugin


async def _reload_mcp(app: FastAPI) -> None:
    """按最新 mcp_servers 配置全量热重载 MCP 插件（断开后重连）。"""
    plugin = await _get_mcp_plugin(app)
    if plugin is not None:
        await plugin.reload(app.state.config.mcp_servers)


def _merge_server_env(sc: "MCPServerConfig", env_updates: dict[str, Any]) -> None:
    """合并单个 MCP server 的 env：掩码回传或空值不修改，否则更新对应 key。

    与 providers 的 api_key 掩码规则一致：前端回传的掩码值（与当前掩码
    相等）表示「不修改」，避免 token 类 env 被误覆盖。
    """
    for k, v in env_updates.items():
        k = str(k)
        if v is None or v == "":
            continue
        current_mask = _mask_env_value(sc.env.get(k))
        if isinstance(v, str) and v == current_mask:
            continue
        sc.env[k] = str(v)


async def _mcp_status_items(app: FastAPI) -> list[dict[str, Any]]:
    """合并 config.mcp_servers（配置字段 + env 掩码）与 manager 运行时状态。

    以配置为骨架逐 server 生成 GET 表示；plugin 存在时用
    ``manager.get_status()`` 按 name 填充 status/error/tool_count/tools，
    否则按配置推断：disabled → "disabled"，其余 → "disconnected"。
    """
    cfg: NexusConfig = app.state.config
    runtime: dict[str, dict[str, Any]] = {}
    plugin = await _get_mcp_plugin(app)
    if plugin is not None:
        try:
            runtime = {s["name"]: s for s in plugin.manager.get_status()}
        except Exception:
            logger.warning("读取 MCP 运行状态失败", exc_info=True)

    result: list[dict[str, Any]] = []
    for name, sc in cfg.mcp_servers.items():
        item: dict[str, Any] = {
            "name": name,
            "command": sc.command,
            "args": list(sc.args),
            "env": _mask_env(sc.env),
            "url": sc.url,
            "enabled": sc.enabled,
            "transport": sc.transport,
        }
        state = runtime.get(name)
        if state is not None:
            item.update(
                {
                    "status": state.get("status"),
                    "error": state.get("error"),
                    "tool_count": state.get("tool_count", 0),
                    "tools": list(state.get("tools") or []),
                }
            )
        else:
            item.update(
                {
                    "status": "disabled" if not sc.enabled else "disconnected",
                    "error": None,
                    "tool_count": 0,
                    "tools": [],
                }
            )
        result.append(item)
    return result


async def _mcp_status_item(app: FastAPI, name: str) -> dict[str, Any] | None:
    """取单个 MCP server 的 GET 表示；未配置返回 None。"""
    for s in await _mcp_status_items(app):
        if s["name"] == name:
            return s
    return None


# ------------------------------------------------------------------
# WebSocket 聊天会话
# ------------------------------------------------------------------


class _ChatConnection:
    """单个 /ws/chat 连接的会话状态。

    每个连接独立维护一份对话历史（list[dict]），通过 restore/reset
    由前端控制上下文恢复与清空；run 完成后以 state.messages
    （过滤 system）刷新历史，实现多轮上下文。

    持久化：连接创建时生成 8 位 session_id，每次 run 成功后以
    覆盖式写入 SessionManager（同一连接多次 run 更新同一会话文件），
    使桌面端会话出现在历史会话列表中；保存失败仅记日志。

    并发保护：同一连接一次只允许一个 run。run 在后台 task 中执行，
    接收循环保持活跃，run 进行中再收到 message 直接回错误。
    """

    def __init__(
        self, ws: WebSocket, agent: Agent, session_manager: SessionManager, app: FastAPI | None = None
    ) -> None:
        self.ws = ws
        self.agent = agent  # 初始 agent（连接建立时）
        self.session_manager = session_manager
        self.app = app  # 用于动态获取最新 agent（配置变更后）
        # 8 位会话 ID（uuid4 前缀），贯穿连接生命周期，用于覆盖式保存
        self.session_id: str = str(uuid.uuid4())[:_SESSION_ID_LENGTH]
        self.history: list[dict[str, Any]] = []
        self._run_task: asyncio.Task | None = None

    @property
    def current_agent(self) -> Agent:
        """获取当前 agent（优先从 app.state 获取最新版本）。"""
        if self.app is not None:
            try:
                return self.app.state.agent
            except Exception:
                pass
        return self.agent

    @property
    def running(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    async def handle(self, data: dict[str, Any]) -> None:
        """分发一条客户端消息。"""
        msg_type = data.get("type")

        if msg_type == "reset":
            self.history = []
            # 重置时生成新的 session_id，避免覆盖上一个会话
            self.session_id = str(uuid.uuid4())[:_SESSION_ID_LENGTH]
            await self.ws.send_json({"type": "reset_ok"})

        elif msg_type == "restore":
            messages = data.get("messages") or []
            self.history = [m for m in messages if isinstance(m, dict)]
            # 恢复指定会话：用前端传来的 session_id 覆盖，使后续保存写到原会话文件
            session_id = data.get("session_id")
            if isinstance(session_id, str) and session_id:
                self.session_id = session_id
            await self.ws.send_json(
                {"type": "restore_ok", "message_count": len(self.history)}
            )

        elif msg_type == "slash_command":
            command = str(data.get("command", "")).lower().strip()
            await self._handle_slash_command(command)

        elif msg_type == "message":
            if self.running:
                await self.ws.send_json(
                    {"type": "error", "message": "上一个任务仍在执行中"}
                )
                return
            content = str(data.get("content", ""))
            self._run_task = asyncio.create_task(self._run(content))

        else:
            await self.ws.send_json(
                {"type": "error", "message": f"未知消息类型: {msg_type}"}
            )

    async def cancel(self) -> None:
        """连接关闭时取消未完成的 run。"""
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):
                pass

    def _save_session(self) -> None:
        """持久化当前对话到 SessionManager（覆盖式保存）。

        与 CLI 的 REPL 退出保存（nexus.cli.repl._handle_exit）保持一致：
        以当前连接历史构造 AgentState，传入连接级 session_id，
        同一连接多次 run 更新同一个会话文件。
        保存失败仅记日志，不影响对话流程。
        """
        try:
            state = AgentState(
                task="desktop session",
                messages=list(self.history),
            )
            self.session_manager.save(
                state,
                metadata={"mode": "desktop"},
                session_id=self.session_id,
            )
        except Exception:
            logger.warning("Failed to save desktop session", exc_info=True)

    async def _handle_slash_command(self, command: str) -> None:
        """处理斜杠命令，返回结果给前端。

        支持的命令：
        /tools  - 列出已注册的工具
        /clear  - 清空对话上下文
        /help   - 显示帮助信息
        /sessions - 列出历史会话
        """
        if command == "/tools":
            agent = self.current_agent
            tools = list(agent.tool_registry.list())
            tool_list = [
                {"name": t.name, "description": t.description}
                for t in tools
            ]
            await self.ws.send_json({
                "type": "slash_command_result",
                "command": "/tools",
                "title": "已注册工具",
                "content": tool_list,
            })

        elif command == "/clear":
            self.history = []
            self.session_id = str(uuid.uuid4())[:_SESSION_ID_LENGTH]
            await self.ws.send_json({
                "type": "slash_command_result",
                "command": "/clear",
                "title": "上下文已清空",
                "content": "对话历史已清除，可以开始新的对话。",
            })

        elif command == "/help":
            help_text = (
                "**可用命令：**\n\n"
                "- `/tools` — 列出当前已注册的工具\n"
                "- `/clear` — 清空对话上下文\n"
                "- `/help` — 显示此帮助信息\n"
                "- `/sessions` — 列出历史会话\n\n"
                "直接输入文本即可与 Nexus Agent 对话。"
            )
            await self.ws.send_json({
                "type": "slash_command_result",
                "command": "/help",
                "title": "帮助",
                "content": help_text,
            })

        elif command == "/sessions":
            sessions = self.session_manager.list_sessions()
            session_list = [
                {
                    "id": s.get("id", ""),
                    "summary": s.get("summary", ""),
                    "timestamp": s.get("timestamp", ""),
                    "message_count": s.get("message_count", 0),
                }
                for s in sessions[:20]  # 最多显示 20 条
            ]
            await self.ws.send_json({
                "type": "slash_command_result",
                "command": "/sessions",
                "title": "历史会话",
                "content": session_list,
            })

        else:
            await self.ws.send_json({
                "type": "slash_command_result",
                "command": command,
                "title": "未知命令",
                "content": f"未知命令: {command}，输入 /help 查看帮助",
            })

    async def _run(self, content: str) -> None:
        """执行一轮 Agent.run，期间订阅 EventBus 推送流式事件。

        事件订阅在 run 前进行、run 后（finally）取消，
        避免跨任务泄露（参考 nexus.cli.repl 的 try/finally 模式）。
        """
        ws = self.ws
        tool_index = 0
        usage_acc: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        async def send(payload: dict[str, Any]) -> None:
            await ws.send_json(payload)

        async def on_llm_chunk(event: Event) -> None:
            """流式 chunk → thinking_delta / content_delta。"""
            delta_reasoning = event.payload.get("delta_reasoning", "")
            delta_content = event.payload.get("delta_content", "")
            if delta_reasoning:
                await send({"type": "thinking_delta", "delta": delta_reasoning})
            if delta_content:
                await send({"type": "content_delta", "delta": delta_content})

        async def on_after_llm(event: Event) -> None:
            """LLM 调用后 → 累计 token 用量并推送。"""
            usage = event.payload.get("usage")
            if usage:
                usage_acc["prompt_tokens"] += usage.get("prompt_tokens", 0)
                usage_acc["completion_tokens"] += usage.get("completion_tokens", 0)
                usage_acc["total_tokens"] += usage.get("total_tokens", 0)
                await send({"type": "usage", **usage_acc})

        async def on_after_tool(event: Event) -> None:
            """工具调用后 → 推送 tool_call（result 截断 500 字符）。"""
            nonlocal tool_index
            tool_index += 1
            payload = event.payload
            result = payload.get("result")
            error = payload.get("error")
            result_str = "" if result is None else str(result)
            if len(result_str) > _TOOL_RESULT_MAX_LEN:
                result_str = result_str[: _TOOL_RESULT_MAX_LEN - 3] + "..."
            await send(
                {
                    "type": "tool_call",
                    "name": payload.get("tool_name", "unknown"),
                    "args": payload.get("args", {}),
                    "result": result_str,
                    "error": str(error) if error else None,
                    "success": error is None,
                    "index": tool_index,
                }
            )

        async def on_error(event: Event) -> None:
            """运行时错误 → 推送 error。"""
            error_info = event.payload.get("error")
            await send({"type": "error", "message": f"执行错误: {error_info}"})

        agent = self.current_agent
        events = agent.events
        await events.subscribe(EventType.LLM_CHUNK, on_llm_chunk)
        await events.subscribe(EventType.AFTER_LLM_CALL, on_after_llm)
        await events.subscribe(EventType.AFTER_TOOL_CALL, on_after_tool)
        await events.subscribe(EventType.ON_ERROR, on_error)

        try:
            state = await agent.run(
                content,
                initial_messages=self.history or None,
            )
            # 用 state.messages（过滤 system）刷新历史，实现多轮上下文
            self.history = [
                m for m in state.messages if m.get("role") != "system"
            ]
            # 持久化当前对话（覆盖式），保证 done 到达前端时会话已落盘
            self._save_session()
            await send(
                {
                    "type": "done",
                    "steps": state.current_step,
                    "usage": dict(usage_acc),
                    # 会话 ID 透传给前端（向后兼容：前端不读取也能工作）
                    "session_id": self.session_id,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("WS chat run failed", exc_info=True)
            try:
                await send({"type": "error", "message": f"执行错误: {e}"})
            except Exception:
                pass
        finally:
            await events.unsubscribe(EventType.LLM_CHUNK, on_llm_chunk)
            await events.unsubscribe(EventType.AFTER_LLM_CALL, on_after_llm)
            await events.unsubscribe(EventType.AFTER_TOOL_CALL, on_after_tool)
            await events.unsubscribe(EventType.ON_ERROR, on_error)


# ------------------------------------------------------------------
# 应用工厂
# ------------------------------------------------------------------


def create_app(
    work_dir: str | None = None,
    *,
    config: NexusConfig | None = None,
    llm: BaseLLM | None = None,
    agent: Agent | None = None,
    session_manager: SessionManager | None = None,
    config_save_path: str | None = None,
) -> FastAPI:
    """创建 Nexus Server FastAPI 应用。

    Parameters
    ----------
    work_dir : str | None
        工作目录，用于配置加载与工具的工作根。默认当前目录。
    config : NexusConfig | None
        注入的配置实例（测试用）。None 时调用 load_config() 加载。
    llm : BaseLLM | None
        注入的 LLM 实例（测试用 mock）。None 时由 nexus.core.factory 创建。
    agent : Agent | None
        注入的 Agent 实例。None 时基于 llm + config 创建并注册工具。
    session_manager : SessionManager | None
        注入的会话管理器（测试时指向 tmp_path）。None 时用默认目录。
    config_save_path : str | None
        PUT /api/config 的持久化目标路径。None 时写用户级
        ~/.nexus/nexus.yaml（save_config 默认行为）。

    Returns
    -------
    FastAPI
        组装完成的应用实例。
    """
    work_dir = work_dir or os.getcwd()

    if config is None:
        config = load_config(work_dir=work_dir)
        config.work_dir = work_dir
    if agent is None:
        if llm is not None:
            # 测试注入 LLM 场景：用传入的 llm 构造 Agent，再注册工具
            agent = Agent(
                llm=llm,
                system_prompt=config.system_prompt,
                max_steps=config.max_steps,
                stream=config.stream,
            )
            from nexus.core.factory import register_tools
            register_tools(agent, config)
        else:
            from nexus.core.factory import create_agent
            agent = create_agent(config)
    if session_manager is None:
        session_manager = SessionManager()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        # create_app 本身是 sync，MCP 建连需要事件循环，
        # 因此 MCP 插件安装放到启动钩子内执行（有 enabled server 才安装，
        # 无则 no-op；失败不阻断服务启动）
        try:
            await _get_mcp_plugin(app)
        except Exception:
            logger.warning("MCP 初始化失败，跳过 MCP 能力", exc_info=True)
        yield

    app = FastAPI(title="Nexus Agent Runtime Server", lifespan=_lifespan)
    app.state.config = config
    app.state.agent = agent
    app.state.session_manager = session_manager
    app.state.config_save_path = config_save_path

    # ---------------- REST API（/api 前缀） ----------------

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        cfg: NexusConfig = request.app.state.config
        return {
            "ok": True,
            "provider": cfg.default_provider,
            "model": cfg.model,
        }

    @app.get("/api/config")
    async def get_config(request: Request) -> dict[str, Any]:
        return _config_to_json(request.app.state.config)

    @app.put("/api/config")
    async def put_config(request: Request) -> JSONResponse:
        cfg: NexusConfig = request.app.state.config
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
        _merge_config_json(cfg, data)
        try:
            save_config(cfg, path=request.app.state.config_save_path)
        except Exception as e:
            logger.warning("Failed to save config: %s", e)
            raise HTTPException(status_code=500, detail=f"配置保存失败: {e}")

        # 重建 Agent，使新配置（provider / model / api_key 等）立即生效
        try:
            from nexus.core.factory import create_agent_async

            new_agent = await create_agent_async(cfg)
            request.app.state.config = cfg
            request.app.state.agent = new_agent
            # startup 钩子只在启动时跑一次，agent 重建后需重新安装 MCP
            await _get_mcp_plugin(request.app)
            logger.info(
                "Config saved & agent rebuilt: provider=%s, model=%s",
                cfg.default_provider,
                cfg.model,
            )
        except Exception as e:
            logger.warning("Failed to rebuild agent after config save: %s", e)
            # 配置已落盘，但运行时实例未更新；下次重启会加载新配置

        return JSONResponse(_config_to_json(cfg))

    @app.get("/api/sessions")
    async def list_sessions(request: Request) -> list[dict[str, Any]]:
        mgr: SessionManager = request.app.state.session_manager
        return mgr.list_sessions()

    @app.get("/api/sessions/{session_id}")
    async def get_session(request: Request, session_id: str) -> dict[str, Any]:
        mgr: SessionManager = request.app.state.session_manager
        state = mgr.load(session_id)
        if state is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        # 从列表中取元信息（summary / timestamp / message_count）
        meta = next(
            (s for s in mgr.list_sessions() if s["id"] == session_id), {}
        )
        return {**meta, "messages": list(state.messages)}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(request: Request, session_id: str) -> dict[str, bool]:
        mgr: SessionManager = request.app.state.session_manager
        ok = mgr.delete(session_id, delete_logs=True)
        return {"ok": ok}

    @app.get("/api/tools")
    async def list_tools(request: Request) -> list[dict[str, Any]]:
        """返回所有已注册工具：内置工具（origin=builtin）+ MCP 工具（origin=mcp）。

        主路径以 ``agent.tool_registry`` 为准（含 MCP 远端工具与动态注册的
        工具）；registry 为空时退回内置工具静态构造作为兜底，保证前端
        仍能拿到内置工具清单。
        """
        try:
            registered = list(request.app.state.agent.tool_registry.list())
        except Exception:
            registered = []
        if registered:
            result: list[dict[str, Any]] = []
            for tool in registered:
                name = tool.name
                if name.startswith("mcp__"):
                    # 命名约定 mcp__{server}__{tool}，split 取第 2 段为来源 server
                    parts = name.split("__")
                    result.append(
                        {
                            "name": name,
                            "description": tool.description,
                            "schema": tool.schema,
                            "origin": "mcp",
                            "server": parts[1] if len(parts) >= 2 else None,
                        }
                    )
                else:
                    result.append(
                        {
                            "name": name,
                            "description": tool.description,
                            "schema": tool.schema,
                            "origin": "builtin",
                            "server": None,
                        }
                    )
            return result

        # 兜底：registry 为空时退回内置工具静态构造
        from nexus.tools.file_tools import (
            ReadFileTool,
            WriteFileTool,
            ListDirTool,
            SearchContentTool,
        )
        from nexus.tools.shell_tool import ShellTool

        cfg: NexusConfig = request.app.state.config
        all_tools = {
            "read_file": ReadFileTool(work_dir=cfg.work_dir or os.getcwd()),
            "write_file": WriteFileTool(work_dir=cfg.work_dir or os.getcwd()),
            "list_dir": ListDirTool(),
            "search_content": SearchContentTool(),
            "shell": ShellTool(work_dir=cfg.work_dir or os.getcwd()),
        }
        return [
            {
                "name": name,
                "description": tool.description,
                "schema": tool.schema,
                "origin": "builtin",
                "server": None,
            }
            for name, tool in all_tools.items()
        ]

    # ---------------- MCP server 管理 ----------------

    def _save_mcp_config(request: Request) -> None:
        """持久化当前配置（mcp_servers 变更后统一调用）。"""
        try:
            save_config(
                request.app.state.config,
                path=request.app.state.config_save_path,
            )
        except Exception as e:
            logger.warning("Failed to save config: %s", e)
            raise HTTPException(status_code=500, detail=f"配置保存失败: {e}")

    @app.get("/api/mcp")
    async def list_mcp_servers(request: Request) -> list[dict[str, Any]]:
        """MCP server 列表：配置（env 脱敏）与运行时状态合并。"""
        return await _mcp_status_items(request.app)

    @app.post("/api/mcp")
    async def create_mcp_server(request: Request) -> dict[str, Any]:
        """新增 MCP server：校验 → 写配置 → 持久化 → 热重载。"""
        from nexus.cli.config import MCPServerConfig

        cfg: NexusConfig = request.app.state.config
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

        name = str(data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name 不能为空")
        if name in cfg.mcp_servers:
            raise HTTPException(status_code=400, detail=f"MCP server '{name}' 已存在")

        command = data.get("command")
        url = data.get("url")
        # command 与 url 至少提供一个（stdio / http 两种传输）
        if not command and not url:
            raise HTTPException(
                status_code=400, detail="command 与 url 至少提供一个"
            )

        cfg.mcp_servers[name] = MCPServerConfig(
            command=str(command) if command else None,
            args=[str(a) for a in (data.get("args") or [])],
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            url=str(url) if url else None,
            enabled=bool(data.get("enabled", True)),
        )
        _save_mcp_config(request)
        await _reload_mcp(request.app)
        return await _mcp_status_item(request.app, name)

    @app.put("/api/mcp/{name}")
    async def update_mcp_server(request: Request, name: str) -> dict[str, Any]:
        """更新 MCP server 配置子集并热重载（env 支持掩码回传）。"""
        cfg: NexusConfig = request.app.state.config
        sc = cfg.mcp_servers.get(name)
        if sc is None:
            raise HTTPException(status_code=404, detail=f"MCP server '{name}' 不存在")

        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

        # 先按子集计算目标值，校验通过后再落配置，避免 400 时残留半更新状态
        new_enabled = sc.enabled
        if "enabled" in data and data["enabled"] is not None:
            new_enabled = bool(data["enabled"])
        new_command = sc.command
        if "command" in data:
            new_command = str(data["command"]) if data["command"] else None
        new_url = sc.url
        if "url" in data:
            new_url = str(data["url"]) if data["url"] else None
        # command / url 全被清空时配置不可连接，提前拦截
        if new_enabled and not new_command and not new_url:
            raise HTTPException(
                status_code=400, detail="command 与 url 至少保留一个"
            )

        sc.enabled = new_enabled
        sc.command = new_command
        sc.url = new_url
        if "args" in data:
            sc.args = [str(a) for a in (data["args"] or [])]
        env_updates = data.get("env")
        if isinstance(env_updates, dict):
            _merge_server_env(sc, env_updates)

        _save_mcp_config(request)
        await _reload_mcp(request.app)
        return await _mcp_status_item(request.app, name)

    @app.delete("/api/mcp/{name}")
    async def delete_mcp_server(request: Request, name: str) -> dict[str, bool]:
        """移除 MCP server：删配置 → 持久化 → 热重载（断开 + 注销工具）。"""
        cfg: NexusConfig = request.app.state.config
        if name not in cfg.mcp_servers:
            raise HTTPException(status_code=404, detail=f"MCP server '{name}' 不存在")
        cfg.mcp_servers.pop(name)
        _save_mcp_config(request)
        await _reload_mcp(request.app)
        return {"ok": True}

    @app.post("/api/mcp/{name}/reconnect")
    async def reconnect_mcp_server(request: Request, name: str) -> JSONResponse:
        """重连 MCP server 并刷新工具列表。"""
        cfg: NexusConfig = request.app.state.config
        if name not in cfg.mcp_servers:
            raise HTTPException(status_code=404, detail=f"MCP server '{name}' 不存在")
        plugin = await _get_mcp_plugin(request.app)
        if plugin is None:
            raise HTTPException(status_code=400, detail="MCP 未启用")
        ok = await plugin.manager.reconnect(
            name, request.app.state.agent.tool_registry
        )
        if not ok:
            state = await _mcp_status_item(request.app, name)
            error = state.get("error") if state else None
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": error or f"MCP server '{name}' 重连失败",
                },
            )
        return JSONResponse({"ok": True})

    # ---------------- WebSocket 聊天 ----------------

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket) -> None:
        await ws.accept()
        conn = _ChatConnection(
            ws, ws.app.state.agent, ws.app.state.session_manager, ws.app
        )
        try:
            while True:
                data = await ws.receive_json()
                if isinstance(data, dict):
                    await conn.handle(data)
                else:
                    await ws.send_json(
                        {"type": "error", "message": "消息必须是 JSON 对象"}
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await conn.cancel()

    # ---------------- 前端静态资源（desktop/dist） ----------------

    # 项目根 = nexus/server/app.py 向上两级
    project_root = Path(__file__).resolve().parents[2]
    dist_dir = project_root / "desktop" / "dist"
    if dist_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="desktop")
        logger.info("Mounted desktop dist at /: %s", dist_dir)

    return app
