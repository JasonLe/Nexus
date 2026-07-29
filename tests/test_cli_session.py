"""测试会话管理：SessionManager 的保存、加载、列表、删除、截断和恢复。

覆盖范围
--------
- save + load 往返：保存后加载 state 一致
- load 不存在的会话返回 None
- list_sessions 按时间倒序列出
- delete 删除会话后加载返回 None
- auto_truncate 超长历史自动截断
- load_latest 返回最近一次保存的会话
"""

import json
import time

import pytest

from nexus.core.state.types import AgentState
from nexus.cli.session import SessionManager


def _make_state(
    task: str = "test task",
    messages: list[dict] | None = None,
    steps: int = 0,
) -> AgentState:
    """辅助函数：创建用于测试的 AgentState 实例。"""
    state = AgentState(task=task)
    if messages:
        state.messages = messages
    for i in range(steps):
        state.add_step(  # type: ignore[attr-defined]
            type("Step", (), {"step_id": f"step_{i}", "step_type": "llm_call"})
        )
    return state


# ---------------------------------------------------------------------------
# save + load 往返
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    """测试 save 和 load 的往返正确性。"""

    def test_save_and_load(self, tmp_path):
        """保存后再加载，AgentState 字段应一致。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        state = _make_state(
            task="debug the auth module",
            messages=[
                {"role": "user", "content": "There is a bug in login"},
                {"role": "assistant", "content": "Let me check the code"},
            ],
        )

        session_id = sm.save(state)
        loaded = sm.load(session_id)

        assert loaded is not None
        assert loaded.task == "debug the auth module"
        assert len(loaded.messages) == 2
        assert loaded.messages[0]["role"] == "user"
        assert loaded.messages[0]["content"] == "There is a bug in login"
        assert loaded.messages[1]["role"] == "assistant"

    def test_save_with_metadata(self, tmp_path):
        """保存时附带 metadata 应在会话文件中保留。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        state = _make_state(task="test")

        session_id = sm.save(state, metadata={"project": "nexus", "version": "1.0"})

        # 直接读取文件验证 metadata
        filepath = tmp_path / f"{session_id}.json"
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["metadata"]["project"] == "nexus"
        assert data["metadata"]["version"] == "1.0"


class TestLoadNonexistent:
    """测试加载不存在的会话。"""

    def test_load_nonexistent(self, tmp_path):
        """加载不存在的会话 ID 应返回 None。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        result = sm.load("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# list_sessions 测试
# ---------------------------------------------------------------------------


class TestListSessions:
    """测试会话列表。"""

    def test_list_sessions(self, tmp_path):
        """保存多个会话后，list 应按时间倒序返回。"""
        sm = SessionManager(sessions_dir=str(tmp_path))

        id1 = sm.save(_make_state(task="first"))
        time.sleep(0.01)  # 确保文件修改时间有差异
        id2 = sm.save(_make_state(task="second"))

        sessions = sm.list_sessions()
        assert len(sessions) >= 2

        # 最新的排在前面
        assert sessions[0]["id"] == id2
        assert sessions[1]["id"] == id1

    def test_list_sessions_summary(self, tmp_path):
        """列表中的每条会话应包含 summary 和 message_count。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        state = _make_state(
            task="hello task",
            messages=[{"role": "user", "content": "Hello, Nexus!"}],
        )

        session_id = sm.save(state)
        sessions = sm.list_sessions()

        found = next(s for s in sessions if s["id"] == session_id)
        assert found["summary"] == "Hello, Nexus!"
        assert found["message_count"] == 1


class TestEmptySessions:
    """测试无会话时的行为。"""

    def test_list_sessions_empty(self, tmp_path):
        """无任何会话时 list 应返回空列表。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        assert sm.list_sessions() == []

    def test_load_latest_empty(self, tmp_path):
        """无任何会话时 load_latest 应返回 (None, None)。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        sid, state = sm.load_latest()
        assert sid is None
        assert state is None


# ---------------------------------------------------------------------------
# delete 测试
# ---------------------------------------------------------------------------


class TestDeleteSession:
    """测试会话删除。"""

    def test_delete_session(self, tmp_path):
        """删除会话后，load 应返回 None。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        state = _make_state(task="to be deleted")
        session_id = sm.save(state)

        assert sm.delete(session_id) is True
        assert sm.load(session_id) is None

    def test_delete_nonexistent(self, tmp_path):
        """删除不存在的会话应返回 False。"""
        sm = SessionManager(sessions_dir=str(tmp_path))
        assert sm.delete("nonexistent") is False


# ---------------------------------------------------------------------------
# auto_truncate 测试
# ---------------------------------------------------------------------------


class TestAutoTruncate:
    """测试超长历史的自动截断。"""

    def test_auto_truncate(self, tmp_path):
        """超长历史保存时应被自动截断为最近 N 轮。"""
        sm = SessionManager(sessions_dir=str(tmp_path))

        # 创建 120 条非 system 消息（60 轮对话），超过默认 50 轮上限
        messages = [{"role": "system", "content": "You are a helpful assistant."}]
        for i in range(60):
            messages.append({"role": "user", "content": f"question {i}"})
            messages.append({"role": "assistant", "content": f"answer {i}"})

        state = _make_state(task="long conversation", messages=messages)

        session_id = sm.save(state, auto_truncate=True)
        loaded = sm.load(session_id)

        assert loaded is not None
        # system 消息始终保留
        system_count = sum(1 for m in loaded.messages if m["role"] == "system")
        assert system_count == 1

        # 非 system 消息应被截断到最多 100 条（50 轮 × 2）
        non_system = [m for m in loaded.messages if m["role"] != "system"]
        assert len(non_system) <= 100

    def test_auto_truncate_preserves_recent(self, tmp_path):
        """截断应保留最近的消息，丢弃最旧的消息。"""
        sm = SessionManager(sessions_dir=str(tmp_path))

        messages = [{"role": "system", "content": "system prompt"}]
        for i in range(60):
            messages.append({"role": "user", "content": f"q{i}"})
            messages.append({"role": "assistant", "content": f"a{i}"})

        state = _make_state(task="test", messages=messages)

        session_id = sm.save(state, auto_truncate=True)
        loaded = sm.load(session_id)

        assert loaded is not None
        non_system = [m for m in loaded.messages if m["role"] != "system"]

        # 最旧的非 system 消息 q0/a0 应被丢弃
        oldest_contents = {m["content"] for m in non_system}
        assert "q0" not in oldest_contents
        assert "a0" not in oldest_contents

        # 最新的消息应保留
        assert "q59" in oldest_contents
        assert "a59" in oldest_contents

    def test_no_truncate_short_history(self, tmp_path):
        """短于上限的历史不会被截断。"""
        sm = SessionManager(sessions_dir=str(tmp_path))

        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        state = _make_state(task="short", messages=messages)

        session_id = sm.save(state, auto_truncate=True)
        loaded = sm.load(session_id)

        assert loaded is not None
        assert len(loaded.messages) == 3


# ---------------------------------------------------------------------------
# load_latest 测试
# ---------------------------------------------------------------------------


class TestLoadLatest:
    """测试 load_latest 恢复最近会话。"""

    def test_load_latest(self, tmp_path):
        """保存多个会话后，load_latest 应返回最新保存的。"""
        sm = SessionManager(sessions_dir=str(tmp_path))

        sm.save(_make_state(task="session A"))
        time.sleep(0.01)
        sm.save(_make_state(task="session B"))
        time.sleep(0.01)
        sm.save(_make_state(task="session C"))

        sid, state = sm.load_latest()
        assert sid is not None
        assert state is not None
        assert state.task == "session C"
