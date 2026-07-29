"""事件总线实现 —— Async 发布/订阅模式。

设计思路
--------
EventBus 是框架的事件分发中枢。采用 async pub/sub 模式：

1. **Async 优先**：所有 handler 通过 ``asyncio.create_task`` 并发执行，
   不阻塞事件发布线程。同步 handler 会被自动包装为 async。

2. **异常隔离**：单个 handler 抛出异常不会中断其他 handler 的执行，
   异常被捕获后记录 warning 日志但不重新抛出。这是关键设计决策 ——
   事件监控不应影响 Agent 主流程。

3. **弱引用（MVP 阶段暂不实现）**：handler 注册后由调用方管理生命周期，
   避免内存泄漏。当前 MVP 假设调用方会在不需要时主动 unsubscribe()。

4. **未来 Web UI 集成**：可以轻松派生 WebSocketEventBus，
   在 publish 中将 Event 序列化后通过 WebSocket 推送到前端，
   按 run_id 和 step 序列号实时渲染 Agent 思考/工具调用过程。

实现约定
--------
- subscribe() 支持同步函数和 async 协程两种 handler
- publish() 并发调用所有匹配的 handler
- handler 异常被捕获并记录，不向上传播
- unsubscribe() 移除指定 handler
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Callable, Awaitable

from nexus.core.event.event_types import EventType
from nexus.core.event.types import Event
from nexus.logging import get_logger

logger = get_logger(__name__)


EventHandler = Callable[[Event], Awaitable[None]] | Callable[[Event], None]
"""事件处理器类型。

可接受同步或异步的 callable，接收一个 Event 参数且无返回值。
同步 handler 在 EventBus 实现中被包装为可 await（如通过
``asyncio.to_thread`` 或直接调用），不应阻塞事件循环。
"""


class EventBus:
    """事件总线 —— Async 发布/订阅模式。

    提供三个核心操作：publish（发布事件）、subscribe（订阅事件类型）、
    unsubscribe（取消订阅）。

    所有 handler 通过 ``asyncio.TaskGroup`` 并发执行，单个 handler 异常
    不影响其他 handler 或主流程。

    使用示例
    --------
    >>> bus = EventBus()
    >>> await bus.subscribe(EventType.BEFORE_LLM_CALL, my_logger)
    >>> event = Event(type=EventType.BEFORE_LLM_CALL, payload={"model": "gpt-4"})
    >>> await bus.publish(event)
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)

    async def publish(self, event: Event) -> None:
        """发布事件到所有订阅该事件类型的 handler。

        查找所有订阅了 ``event.type`` 的 handler，在 ``asyncio.TaskGroup``
        中并发执行。每个 handler 的异常被捕获后记录 warning 日志，不级联传播。

        Parameters
        ----------
        event : Event
            待发布的事件实例。
        """
        handlers = self._handlers.get(event.type)
        if not handlers:
            return

        # 并发调度：TaskGroup 上下文确保所有子任务在线程安全的环境中执行
        async with asyncio.TaskGroup() as tg:
            for handler in handlers:
                tg.create_task(self._invoke_handler(handler, event))

        logger.debug(
            "publish event_type=%s payload_keys=%s handler_count=%d",
            event.type.name,
            list(event.payload.keys()),
            len(handlers),
        )

    async def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """订阅特定事件类型。

        当 ``event_type`` 对应的事件被 publish 时，handler 将被调用。
        重复订阅同一 (event_type, handler) 对应为幂等操作。

        Parameters
        ----------
        event_type : EventType
            要订阅的事件类型。
        handler : EventHandler
            事件处理器，可以是同步或异步 callable。
        """
        registered = self._handlers[event_type]
        if handler not in registered:
            registered.append(handler)

    async def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """取消订阅。

        取消未注册的 (event_type, handler) 对应为无操作，不抛异常。

        Parameters
        ----------
        event_type : EventType
            要取消订阅的事件类型。
        handler : EventHandler
            要移除的事件处理器。
        """
        registered = self._handlers.get(event_type)
        if registered is None:
            return
        try:
            registered.remove(handler)
        except ValueError:
            pass  # handler 未注册，静默忽略

    async def _invoke_handler(self, handler: EventHandler, event: Event) -> None:
        """调用单个 handler 并做异常隔离。

        根据 handler 是否为 async 协程函数选择调用方式：
        - async handler：await 执行
        - sync handler：直接调用

        Parameters
        ----------
        handler : EventHandler
            待调用的事件处理器。
        event : Event
            事件实例。
        """
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)  # type: ignore[arg-type]
            else:
                handler(event)  # type: ignore[call-arg]
        except Exception:
            handler_name = getattr(handler, "__name__", repr(handler))
            logger.warning(
                "Handler %s raised exception on event_type=%s",
                handler_name,
                event.type.name,
                exc_info=True,
            )
