"""测试 EventBus 的订阅/派发/异常隔离机制。

覆盖范围
--------
- 订阅单个事件类型并发布，验证 handler 被调用
- 同一事件类型注册多个 handler，验证全部被调用
- 不同事件类型独立路由，互不干扰
- async handler 和 sync handler 均能正常执行
- handler 抛异常时不阻断其他 handler（异常隔离）
- 取消订阅后 handler 不再收到事件
- 发布事件但无订阅者时不报错
- payload 正确传递给 handler
"""

import asyncio
import pytest
from nexus.core.event.event_types import EventType
from nexus.core.event.types import Event
from nexus.core.event.event_bus import EventBus


class TestEventBusSubscribeAndPublish:
    """测试 EventBus 基本订阅/发布功能。"""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        """订阅一个事件类型，发布后 handler 应被调用。"""
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(EventType.BEFORE_LLM_CALL, handler)
        event = Event(type=EventType.BEFORE_LLM_CALL, payload={"model": "gpt-4"})
        await bus.publish(event)

        assert len(received) == 1
        assert received[0] is event
        assert received[0].type == EventType.BEFORE_LLM_CALL
        assert received[0].payload["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_multiple_handlers(self):
        """同一事件类型注册多个 handler，全部应被调用。"""
        bus = EventBus()
        calls: list[str] = []

        async def handler_a(event: Event) -> None:
            calls.append("a")

        async def handler_b(event: Event) -> None:
            calls.append("b")

        async def handler_c(event: Event) -> None:
            calls.append("c")

        await bus.subscribe(EventType.AFTER_AGENT_RUN, handler_a)
        await bus.subscribe(EventType.AFTER_AGENT_RUN, handler_b)
        await bus.subscribe(EventType.AFTER_AGENT_RUN, handler_c)

        event = Event(type=EventType.AFTER_AGENT_RUN, payload={"result": "done"})
        await bus.publish(event)

        assert len(calls) == 3
        assert "a" in calls
        assert "b" in calls
        assert "c" in calls

    @pytest.mark.asyncio
    async def test_different_event_types(self):
        """不同事件类型的 handler 各自独立，不互相干扰。"""
        bus = EventBus()
        before_calls: list[Event] = []
        after_calls: list[Event] = []

        async def before_handler(event: Event) -> None:
            before_calls.append(event)

        async def after_handler(event: Event) -> None:
            after_calls.append(event)

        await bus.subscribe(EventType.BEFORE_LLM_CALL, before_handler)
        await bus.subscribe(EventType.AFTER_LLM_CALL, after_handler)

        # 只发布 BEFORE_LLM_CALL
        event_before = Event(type=EventType.BEFORE_LLM_CALL, payload={"model": "gpt-4"})
        await bus.publish(event_before)

        assert len(before_calls) == 1
        assert len(after_calls) == 0

        # 再发布 AFTER_LLM_CALL
        event_after = Event(type=EventType.AFTER_LLM_CALL, payload={"response": "ok"})
        await bus.publish(event_after)

        assert len(before_calls) == 1  # 不变
        assert len(after_calls) == 1


class TestEventBusHandlerTypes:
    """测试 EventBus 对不同类型 handler 的支持。"""

    @pytest.mark.asyncio
    async def test_async_handler(self):
        """async handler 应能正常执行。"""
        bus = EventBus()
        received: list[Event] = []

        async def async_handler(event: Event) -> None:
            # 模拟异步操作
            await asyncio.sleep(0.001)
            received.append(event)

        await bus.subscribe(EventType.BEFORE_TOOL_CALL, async_handler)
        event = Event(type=EventType.BEFORE_TOOL_CALL, payload={"tool_name": "search"})
        await bus.publish(event)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_sync_handler(self):
        """sync handler 应能正常执行。"""
        bus = EventBus()
        received: list[Event] = []

        def sync_handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(EventType.AFTER_TOOL_CALL, sync_handler)
        event = Event(type=EventType.AFTER_TOOL_CALL, payload={"tool_name": "search"})
        await bus.publish(event)

        assert len(received) == 1
        assert received[0].type == EventType.AFTER_TOOL_CALL


class TestEventBusExceptionIsolation:
    """测试 EventBus 异常隔离机制 —— 一个 handler 异常不影响其他。"""

    @pytest.mark.asyncio
    async def test_handler_exception_isolation(self):
        """一个 handler 抛异常时不应阻断其他 handler 执行。"""
        bus = EventBus()
        normal_calls: list[str] = []

        async def failing_handler(event: Event) -> None:
            raise RuntimeError("handler failure")

        async def normal_handler(event: Event) -> None:
            normal_calls.append("ok")

        await bus.subscribe(EventType.ON_FINISH, failing_handler)
        await bus.subscribe(EventType.ON_FINISH, normal_handler)

        event = Event(type=EventType.ON_FINISH, payload={"final_state": "done"})
        # 不应抛出异常
        await bus.publish(event)

        # normal_handler 应仍然被调用
        assert len(normal_calls) == 1
        assert normal_calls[0] == "ok"

    @pytest.mark.asyncio
    async def test_handler_exception_isolation_async(self):
        """async handler 抛异常时也不应阻断其他 async handler。"""
        bus = EventBus()
        normal_calls: list[str] = []

        async def failing_async_handler(event: Event) -> None:
            await asyncio.sleep(0.001)
            raise RuntimeError("async handler failure")

        async def normal_handler(event: Event) -> None:
            normal_calls.append("still_ok")

        await bus.subscribe(EventType.ON_ERROR, failing_async_handler)
        await bus.subscribe(EventType.ON_ERROR, normal_handler)

        event = Event(type=EventType.ON_ERROR, payload={"error": "test"})
        await bus.publish(event)

        assert len(normal_calls) == 1
        assert normal_calls[0] == "still_ok"


class TestEventBusUnsubscribe:
    """测试 EventBus 取消订阅功能。"""

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        """取消订阅后 handler 不应再收到事件。"""
        bus = EventBus()
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(EventType.BEFORE_AGENT_RUN, handler)

        # 发布一次，验证收到
        event1 = Event(type=EventType.BEFORE_AGENT_RUN, payload={"run_id": "run-1"})
        await bus.publish(event1)
        assert len(received) == 1

        # 取消订阅
        await bus.unsubscribe(EventType.BEFORE_AGENT_RUN, handler)

        # 再次发布，不应收到
        event2 = Event(type=EventType.BEFORE_AGENT_RUN, payload={"run_id": "run-2"})
        await bus.publish(event2)
        assert len(received) == 1  # 仍然是 1

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent(self):
        """取消未注册的 handler 不应报错。"""
        bus = EventBus()

        async def handler(event: Event) -> None:
            pass

        # 对空 EventBus 或未注册的 handler 取消订阅，不应抛异常
        await bus.unsubscribe(EventType.BEFORE_LLM_CALL, handler)

        # 即使 event_type 下无任何 handler 也不应报错
        await bus.unsubscribe(EventType.ON_ERROR, handler)


class TestEventBusEdgeCases:
    """测试 EventBus 边界情况。"""

    @pytest.mark.asyncio
    async def test_publish_no_handler(self):
        """发布事件但无订阅者时不应报错。"""
        bus = EventBus()
        event = Event(type=EventType.BEFORE_LLM_CALL, payload={"model": "gpt-4"})
        # 不应抛出异常
        await bus.publish(event)

    @pytest.mark.asyncio
    async def test_event_payload(self):
        """payload 应正确传递给 handler。"""
        bus = EventBus()
        received_payload: dict = {}

        async def handler(event: Event) -> None:
            nonlocal received_payload
            received_payload = event.payload

        await bus.subscribe(EventType.ON_FINISH, handler)

        payload = {
            "run_id": "run-abc-123",
            "final_state": {"task": "completed", "steps": 3},
            "extra_field": "extra_value",
        }
        event = Event(type=EventType.ON_FINISH, payload=payload)
        await bus.publish(event)

        assert received_payload == payload
        assert received_payload["run_id"] == "run-abc-123"
        assert received_payload["final_state"]["task"] == "completed"
        assert received_payload["final_state"]["steps"] == 3

    @pytest.mark.asyncio
    async def test_duplicate_subscribe(self):
        """重复订阅同一个 (event_type, handler) 不应导致重复调用。"""
        bus = EventBus()
        call_count = 0

        async def handler(event: Event) -> None:
            nonlocal call_count
            call_count += 1

        # 多次订阅同一个 handler
        await bus.subscribe(EventType.BEFORE_LLM_CALL, handler)
        await bus.subscribe(EventType.BEFORE_LLM_CALL, handler)
        await bus.subscribe(EventType.BEFORE_LLM_CALL, handler)

        event = Event(type=EventType.BEFORE_LLM_CALL, payload={})
        await bus.publish(event)

        # EventBus 的 register 会去重，所以只调用一次
        assert call_count == 1
