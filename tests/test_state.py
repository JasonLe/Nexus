"""测试 AgentState 和 ToolCallRecord 的序列化/反序列化。

覆盖范围
--------
- 空 AgentState 序列化输出 dict 包含所有必要字段
- 含 steps 的 AgentState 序列化正确嵌套 steps
- AgentState 序列化后反序列化，数据完全一致（round-trip）
- 缺失字段反序列化时使用 dataclass 默认值（向后兼容）
- ToolCallRecord 序列化往返一致
"""

import datetime
from nexus.core.state.types import AgentState, Step, ToolCallRecord


class TestAgentStateSerialization:
    """测试 AgentState 序列化。"""

    def test_serialize_empty_state(self):
        """空 state 序列化后应输出包含所有必要字段的 dict。"""
        state = AgentState(task="test task")
        data = state.serialize()

        assert isinstance(data, dict)
        assert data["task"] == "test task"
        assert data["steps"] == []
        assert data["tool_calls"] == []
        assert data["memory_refs"] == []
        assert data["intermediate_results"] == {}
        assert data["variables"] == {}
        assert data["messages"] == []
        assert data["current_step"] == 0
        assert "run_id" in data
        assert "created_at" in data
        # created_at 应为 ISO 8601 格式字符串
        datetime.datetime.fromisoformat(data["created_at"])

    def test_serialize_with_steps(self):
        """含 steps 的 state 序列化应正确嵌套 steps 数据。"""
        state = AgentState(task="multi-step task")

        tc_record = ToolCallRecord(
            tool_name="search",
            arguments={"query": "weather"},
            result="sunny",
            duration_ms=150.0,
        )
        step = Step(
            step_type="llm_call",
            input_messages=[{"role": "user", "content": "what's the weather?"}],
            output_content="I need to search for weather",
            tool_calls=[tc_record],
            duration_ms=2000.0,
        )
        state.add_step(step)
        state.add_tool_call(tc_record)
        state.add_message("user", "what's the weather?")

        data = state.serialize()

        assert len(data["steps"]) == 1
        serialized_step = data["steps"][0]
        assert serialized_step["step_type"] == "llm_call"
        assert serialized_step["output_content"] == "I need to search for weather"
        assert len(serialized_step["input_messages"]) == 1
        assert serialized_step["input_messages"][0]["role"] == "user"
        assert len(serialized_step["tool_calls"]) == 1
        assert serialized_step["tool_calls"][0]["tool_name"] == "search"
        assert serialized_step["tool_calls"][0]["result"] == "sunny"
        assert "timestamp" in serialized_step

        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["tool_name"] == "search"
        assert data["tool_calls"][0]["result"] == "sunny"

        assert data["messages"] == [{"role": "user", "content": "what's the weather?"}]

    def test_serialize_with_variables(self):
        """含 variables 和 intermediate_results 的 state 序列化正确。"""
        state = AgentState(task="complex task")
        state.variables["_last_llm_response"] = {"content": "hello", "tool_calls": []}
        state.intermediate_results["final_result"] = "done"

        data = state.serialize()

        assert data["variables"]["_last_llm_response"]["content"] == "hello"
        assert data["intermediate_results"]["final_result"] == "done"


class TestAgentStateDeserialization:
    """测试 AgentState 反序列化。"""

    def test_round_trip(self):
        """序列化后反序列化，数据应完全一致。"""
        original = AgentState(task="round-trip test")

        tc_record = ToolCallRecord(
            tool_name="calculator",
            arguments={"expression": "2+2"},
            result=4,
            duration_ms=5.0,
        )
        step1 = Step(
            step_type="llm_call",
            output_content="let me calculate",
        )
        step2 = Step(
            step_type="tool_call",
            tool_calls=[tc_record],
        )
        original.add_step(step1)
        original.add_step(step2)
        original.add_tool_call(tc_record)
        original.add_message("user", "2+2=?")
        original.add_message("assistant", "The answer is 4")
        original.variables["_last_tool_result"] = 4
        original.intermediate_results["final_result"] = 4

        # 序列化
        data = original.serialize()
        # 反序列化
        restored = AgentState.deserialize(data)

        assert restored.task == original.task
        assert len(restored.steps) == len(original.steps)
        assert restored.steps[0].step_type == "llm_call"
        assert restored.steps[0].output_content == "let me calculate"
        assert restored.steps[1].step_type == "tool_call"
        assert len(restored.steps[1].tool_calls) == 1
        assert restored.steps[1].tool_calls[0].tool_name == "calculator"
        assert restored.steps[1].tool_calls[0].result == 4

        assert len(restored.tool_calls) == 1
        assert restored.tool_calls[0].result == 4

        assert len(restored.messages) == 2
        assert restored.messages[0]["role"] == "user"
        assert restored.messages[1]["content"] == "The answer is 4"

        assert restored.variables["_last_tool_result"] == 4
        assert restored.intermediate_results["final_result"] == 4
        assert restored.current_step == original.current_step

    def test_deserialize_missing_fields(self):
        """缺失字段反序列化时应回退到 dataclass 默认值。"""
        # 只提供 task，其他字段全部缺失
        data = {"task": "minimal"}
        state = AgentState.deserialize(data)

        assert state.task == "minimal"
        assert state.steps == []
        assert state.tool_calls == []
        assert state.memory_refs == []
        assert state.intermediate_results == {}
        assert state.variables == {}
        assert state.messages == []
        assert state.current_step == 0
        assert state.run_id is not None and isinstance(state.run_id, str)

    def test_deserialize_empty_dict(self):
        """空 dict 反序列化时应使用所有默认值。"""
        state = AgentState.deserialize({})

        assert state.task == ""
        assert state.steps == []
        assert state.tool_calls == []
        assert state.messages == []
        assert state.current_step == 0
        assert isinstance(state.run_id, str)


class TestToolCallRecordSerialization:
    """测试 ToolCallRecord 序列化/反序列化。"""

    def test_tool_call_record_round_trip(self):
        """ToolCallRecord 序列化往返应完全一致。"""
        original = ToolCallRecord(
            tool_name="web_search",
            arguments={"query": "python", "max_results": 5},
            result={"items": [{"title": "Python.org", "url": "https://python.org"}]},
            error=None,
            duration_ms=123.45,
        )

        # 序列化
        data = original.serialize()
        assert data["tool_name"] == "web_search"
        assert data["arguments"] == {"query": "python", "max_results": 5}
        assert data["result"]["items"][0]["title"] == "Python.org"
        assert data["error"] is None
        assert data["duration_ms"] == 123.45

        # 反序列化
        restored = ToolCallRecord.deserialize(data)

        assert restored.tool_name == original.tool_name
        assert restored.arguments == original.arguments
        assert restored.result == original.result
        assert restored.error == original.error
        assert restored.duration_ms == original.duration_ms

    def test_tool_call_record_with_error(self):
        """含 error 的 ToolCallRecord 序列化往返一致。"""
        original = ToolCallRecord(
            tool_name="broken_tool",
            arguments={},
            result=None,
            error="Connection timeout after 30s",
            duration_ms=30000.0,
        )

        data = original.serialize()
        assert data["error"] == "Connection timeout after 30s"
        assert data["result"] is None

        restored = ToolCallRecord.deserialize(data)
        assert restored.error == "Connection timeout after 30s"
        assert restored.result is None
        assert restored.duration_ms == 30000.0


class TestAgentStateModification:
    """测试 AgentState 状态修改方法。"""

    def test_add_step(self):
        """添加 step 后应出现在 steps 列表中。"""
        state = AgentState(task="test")
        step = Step(step_type="llm_call", output_content="thinking...")
        state.add_step(step)

        assert len(state.steps) == 1
        assert state.steps[0].step_type == "llm_call"
        assert state.steps[0].output_content == "thinking..."

    def test_add_tool_call(self):
        """添加 ToolCallRecord 后应出现在 tool_calls 列表中。"""
        state = AgentState(task="test")
        record = ToolCallRecord(tool_name="search", arguments={}, result="ok")
        state.add_tool_call(record)

        assert len(state.tool_calls) == 1
        assert state.tool_calls[0].tool_name == "search"
        assert state.tool_calls[0].result == "ok"

    def test_add_message(self):
        """添加消息后应出现在 messages 列表中。"""
        state = AgentState(task="test")
        state.add_message("system", "You are helpful")
        state.add_message("user", "Hello")

        assert len(state.messages) == 2
        assert state.messages[0] == {"role": "system", "content": "You are helpful"}
        assert state.messages[1] == {"role": "user", "content": "Hello"}
