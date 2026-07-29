"""Nexus 事件系统抽象层。

提供 Agent 运行时生命周期事件的类型定义、数据结构与事件总线抽象，
支持 Async 发布/订阅模式，用于解耦日志、监控、指标上报、Web UI 等
横切关注点。

导出
----
- EventType：事件类型枚举
- Event：运行时事件 dataclass
- EventBus：事件总线实现类
- EventHandler：事件处理器类型别名
"""

from nexus.core.event.event_types import EventType
from nexus.core.event.types import Event
from nexus.core.event.event_bus import EventBus, EventHandler

__all__ = [
    "EventType",
    "Event",
    "EventBus",
    "EventHandler",
]
