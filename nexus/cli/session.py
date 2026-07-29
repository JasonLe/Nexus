"""Nexus CLI 会话管理 —— 对话历史的保存、加载与恢复。

设计思路
--------
SessionManager 负责将 AgentState 持久化到本地文件系统（~/.nexus/sessions/），
支持跨进程恢复对话上下文。

文件格式：JSON，包含完整 messages、steps、元数据（时间戳、摘要、版本）。

设计原因：
1. 用户在长对话中不想丢失上下文
2. 能够回顾历史交互
3. 通过 --continue 快速恢复上次未完成的对话
4. 自动截断过长历史（保留最近 N 轮），防止恢复时上下文过大

安全考量：
- 会话文件存储在用户目录下，不污染项目
- 自动截断机制防止磁盘膨胀
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.core.state.types import AgentState
from nexus.logging import get_logger

logger = get_logger(__name__)

# 默认保留最近 50 轮对话（每轮 user + assistant，共 100 条非 system 消息）
_DEFAULT_MAX_HISTORY_ROUNDS: int = 50
# 列表查询最多展示的会话条目数
_DEFAULT_LIST_LIMIT: int = 20
# 会话 ID 前缀长度（uuid4 截断）
_SESSION_ID_LENGTH: int = 8
# 摘要截断长度（首条用户消息的前 N 字符）
_SUMMARY_MAX_LENGTH: int = 80


class SessionManager:
    """会话管理器。

    负责对话历史的 CRUD 操作，所有会话存储在 ~/.nexus/sessions/ 目录下。

    设计思路
    --------
    - 每个会话存储为一个独立 JSON 文件，以 session_id 命名。
    - 自动生成摘要（首条用户消息前 80 字符），方便列表浏览。
    - 自动截断过长历史，防止恢复时上下文爆炸。
    """

    def __init__(self, sessions_dir: str | None = None) -> None:
        """初始化会话管理器。

        Parameters
        ----------
        sessions_dir : str | None
            会话存储目录，默认 ~/.nexus/sessions/。
            传入自定义路径可实现隔离（如测试或沙箱环境）。
        """
        if sessions_dir is None:
            home = Path.home()
            self.sessions_dir: Path = home / ".nexus" / "sessions"
        else:
            self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._max_history_rounds: int = _DEFAULT_MAX_HISTORY_ROUNDS

        logger.info(
            "SessionManager initialized",
            extra={"session_id": str(self.sessions_dir)},
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def save(
        self,
        state: AgentState,
        metadata: dict[str, Any] | None = None,
        auto_truncate: bool = True,
    ) -> str:
        """保存会话到文件。

        将 AgentState 序列化为 JSON 并写入 ~/.nexus/sessions/<id>.json。
        自动生成摘要和版本号，支持可选的元数据附加。

        Parameters
        ----------
        state : AgentState
            Agent 执行后的最终状态快照。
        metadata : dict[str, Any] | None
            额外元数据（如任务描述、用户标签），会一并写入会话文件。
        auto_truncate : bool
            是否在持久化前自动截断过长历史（保留最近 50 轮）。
            默认开启，防止会话文件膨胀。

        Returns
        -------
        str
            会话唯一标识符（8 位 uuid 前缀）。
        """
        session_id = str(uuid.uuid4())[:_SESSION_ID_LENGTH]

        # 生成摘要：首条用户消息的前 80 字符，无用户消息则用 task 兜底
        summary = self._generate_summary(state)

        # 选择序列化策略：auto_truncate=True 时先截断再序列化，减少文件体积
        serialized_state = (
            self._truncate_state(state).serialize()
            if auto_truncate
            else state.serialize()
        )

        session_data: dict[str, Any] = {
            "session_id": session_id,
            "version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "metadata": metadata or {},
            "state": serialized_state,
        }

        filepath = self.sessions_dir / f"{session_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

        logger.info(
            "Session saved",
            extra={
                "session_id": session_id,
                "message_count": len(serialized_state.get("messages", [])),
            },
        )
        return session_id

    def load(self, session_id: str) -> AgentState | None:
        """加载指定会话。

        从 JSON 文件反序列化恢复 AgentState。文件不存在时静默返回 None，
        调用方负责处理空结果（如提示用户）。

        Parameters
        ----------
        session_id : str
            会话 ID（不含 .json 扩展名），如 "a1b2c3d4"。

        Returns
        -------
        AgentState | None
            恢复的 AgentState 实例，会话不存在时返回 None。
        """
        filepath = self.sessions_dir / f"{session_id}.json"
        if not filepath.exists():
            logger.warning(
                "Session file not found",
                extra={"session_id": session_id},
            )
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        state = AgentState.deserialize(data["state"])
        logger.info(
            "Session loaded",
            extra={
                "session_id": session_id,
                "message_count": len(state.messages),
            },
        )
        return state

    def load_latest(self) -> tuple[str | None, AgentState | None]:
        """加载最近一次会话。

        CLI 的 ``--continue`` 标志会调用此方法，快速恢复上次未完成的对话。
        按文件修改时间排序，取最新的一个。

        Returns
        -------
        tuple[str | None, AgentState | None]
            (session_id, AgentState) 元组。
            无任何历史会话时返回 (None, None)。
        """
        sessions = self.list_sessions()
        if not sessions:
            logger.info("No sessions found for load_latest")
            return None, None
        latest = sessions[0]
        state = self.load(latest["id"])
        return latest["id"], state

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有历史会话（按时间倒序）。

        扫描 sessions_dir 下所有 .json 文件，加载元数据后按文件修改时间降序排列。
        损坏的 JSON 文件会被静默跳过（continue），不影响整体列表。

        Returns
        -------
        list[dict[str, Any]]
            列表，每项包含以下键：
            - id:            会话 ID（不含扩展名）
            - timestamp:     创建时间（ISO 8601 字符串）
            - summary:       会话摘要文本
            - message_count: 消息条数
            最多返回 20 条。
        """
        sessions: list[dict[str, Any]] = []
        # 按文件修改时间降序排序，保证最新的会话排在前面
        json_files = sorted(
            self.sessions_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for f in json_files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                sessions.append(
                    {
                        "id": f.stem,
                        "timestamp": data.get("created_at", ""),
                        "summary": data.get("summary", ""),
                        "message_count": len(
                            data.get("state", {}).get("messages", [])
                        ),
                    }
                )
            except (json.JSONDecodeError, OSError):
                # 跳过损坏的会话文件，不中断整体列表
                logger.warning(
                    "Skipping corrupt session file",
                    extra={"session_id": f.stem},
                )
                continue

        return sessions[:_DEFAULT_LIST_LIMIT]

    def delete(self, session_id: str) -> bool:
        """删除指定会话。

        永久删除对应的 JSON 文件，不可恢复。文件不存在时返回 False。

        Parameters
        ----------
        session_id : str
            要删除的会话 ID。

        Returns
        -------
        bool
            True 表示删除成功，False 表示文件不存在。
        """
        filepath = self.sessions_dir / f"{session_id}.json"
        if filepath.exists():
            filepath.unlink()
            logger.info(
                "Session deleted",
                extra={"session_id": session_id},
            )
            return True
        logger.warning(
            "Session file not found for deletion",
            extra={"session_id": session_id},
        )
        return False

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _generate_summary(self, state: AgentState) -> str:
        """从 AgentState 生成会话摘要。

        遍历 messages 列表找到第一条 role=="user" 的消息，
        取前 80 字符作为摘要。无用户消息时回退到 task 字段。

        Parameters
        ----------
        state : AgentState
            待生成摘要的状态快照。

        Returns
        -------
        str
            不超过 80 字符的摘要文本。
        """
        for msg in state.messages:
            if msg.get("role") == "user":
                content: str = msg.get("content", "")
                if len(content) > _SUMMARY_MAX_LENGTH:
                    return content[:_SUMMARY_MAX_LENGTH] + "..."
                return content
        return state.task[:_SUMMARY_MAX_LENGTH] if state.task else "(empty session)"

    def _truncate_state(self, state: AgentState) -> AgentState:
        """截断过长历史，保留最近 N 轮对话 + system prompt。

        截断策略：
        1. 保留所有 system 消息（提示词模板，不能丢失）
        2. 非 system 消息按「每轮 2 条（user + assistant）」计算，
           保留最近 self._max_history_rounds 轮
        3. 状态变量、步骤记录等不受影响，仅裁剪 messages 列表

        注意：此方法返回新的 AgentState 实例，不会修改原始 state。

        Parameters
        ----------
        state : AgentState
            原始状态快照。

        Returns
        -------
        AgentState
            截断后的状态副本。
        """
        # 每轮对话由 user + assistant 两条消息组成（tool 消息不计入轮次）
        max_messages = self._max_history_rounds * 2
        messages = state.messages

        # 分离 system 和非 system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        original_count = len(non_system)

        # 超过上限时只保留尾部最近 N 轮
        if len(non_system) > max_messages:
            non_system = non_system[-max_messages:]
            logger.info(
                "Truncated conversation history",
                extra={
                    "original_count": original_count,
                    "truncated_count": len(non_system),
                    "max_rounds": self._max_history_rounds,
                },
            )

        # 创建副本，避免污染原始 state
        truncated = AgentState(
            task=state.task,
            steps=list(state.steps),
            tool_calls=list(state.tool_calls),
            memory_refs=list(state.memory_refs),
            intermediate_results=dict(state.intermediate_results),
            variables=dict(state.variables),
            messages=system_msgs + non_system,
            run_id=state.run_id,
            created_at=state.created_at,
            current_step=state.current_step,
        )
        return truncated
