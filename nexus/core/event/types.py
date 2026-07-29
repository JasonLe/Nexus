"""运行时事件数据结构。

设计思路
--------
- 使用 dataclass 而非普通 dict，保证字段类型安全并提供 IDE 自动补全。
- Event 是值对象：创建后字段不应被修改，由 Runtime 在生命周期节点创建并发布。
- timestamp 使用 UTC 时间保证分布式场景下时间一致性。
- run_id / step 用于关联事件与运行实例，便于日志追踪和 Web UI 按运行分组展示。

扩展点
------
- 可按需增加 metadata 字段承载公共上下文（如 trace_id、parent_run_id）。
- payload 为 dict[str, Any]，各 EventType 的标准 payload 约定见 Event.payload 的 docstring。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nexus.core.event.event_types import EventType


@dataclass
class Event:
    """运行时事件，由 Runtime 在关键生命周期节点派发。

    每个 Event 实例携带事件类型、业务数据 payload、以及运行上下文
    （run_id、step），由 EventBus 路由到已订阅的 handler。

    Attributes
    ----------
    type : EventType
        事件类型枚举值，决定 handler 路由。
    payload : dict[str, Any]
        事件携带的业务数据。不同 EventType 有约定的标准字段，
        见下方 payload 字段详细说明。
    timestamp : datetime
        事件发布时间（UTC），用于排序和前端时间线展示。
    run_id : str
        关联的 Agent 运行实例 ID，用于分组和追踪。
    step : int
        当前运行内的步数计数器，从 1 开始递增。
    """

    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    """事件携带的业务数据。

    不同 EventType 时 payload 的标准字段约定：

    - ``BEFORE_AGENT_RUN``：
      ``agent_name`` (str)、``session_id`` (str)、``run_id`` (str)
    - ``AFTER_AGENT_RUN``：
      ``agent_name`` (str)、``result`` (Any)、``run_id`` (str)
    - ``BEFORE_LLM_CALL``：
      ``model`` (str)、``provider`` (str)、``messages`` (list)、``tools`` (list | None)
    - ``AFTER_LLM_CALL``：
      ``model`` (str)、``provider`` (str)、``response`` (Any)、``usage`` (dict | None)
    - ``BEFORE_TOOL_CALL``：
      ``tool_name`` (str)、``tool_call_id`` (str)、``args`` (dict)
    - ``AFTER_TOOL_CALL``：
      ``tool_name`` (str)、``tool_call_id`` (str)、``result`` (Any)、``error`` (str | None)
    - ``ON_ERROR``：
      ``error`` (Exception)、``traceback`` (str）、``recoverable`` (bool)、``run_id`` (str)
    - ``ON_FINISH``：
      ``run_id`` (str)、``final_state`` (str)
    """

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """事件发布时间（UTC 时区）。"""

    run_id: str = ""
    """关联的 Agent 运行实例 ID。"""

    step: int = 0
    """当前运行内的步数计数器，从 1 开始递增。"""
