"""测试 Nexus Server 后端适配层（REST API + WebSocket 聊天）。

覆盖范围
--------
- GET /api/health 健康检查
- GET /api/config 返回结构且 api_key 脱敏（不暴露完整明文）
- PUT /api/config 合并更新并持久化（save_config 重定向到 tmp_path）
- GET /api/sessions、GET /api/sessions/{id}、DELETE /api/sessions/{id}
- GET /api/tools 返回已注册工具
- WS /ws/chat：restore → message（thinking_delta/content_delta → done）→
  多轮历史保持 → reset 生效
- WS 并发保护：run 进行中再发 message 收到错误
- WS 持久化：run 成功后会话写入 SessionManager（done 携带 session_id），
  同一连接多次 run 覆盖同一会话文件
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from nexus.cli.config import NexusConfig, ProviderConfig
from nexus.cli.session import SessionManager
from nexus.core.state.types import AgentState
from nexus.llm.base import BaseLLM, LLMChunk, LLMResponse, UsageStats
from nexus.server.app import create_app


# ---------------------------------------------------------------------------
# Mock LLM —— 流式产出固定 reasoning/content，记录每次收到的 messages
# ---------------------------------------------------------------------------


class MockLLM(BaseLLM):
    """Mock LLM。

    Parameters
    ----------
    reply : str
        固定的回复文本（content 部分）。
    delay : float
        stream_chat 首个 chunk 前的延迟（秒），用于模拟长任务，
        验证「run 进行中再发 message 被拒绝」的并发保护。
    """

    def __init__(self, reply: str = "你好，我是 Nexus。", delay: float = 0.0) -> None:
        super().__init__()
        self.reply = reply
        self.delay = delay
        self.model = "mock-model"
        # 记录每次 chat/stream_chat 收到的 messages，用于断言多轮历史
        self.calls: list[list[dict]] = []

    async def chat(self, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
        return LLMResponse(
            content=self.reply,
            usage=UsageStats(prompt_tokens=3, completion_tokens=5, total_tokens=8),
            model=self.model,
            finish_reason="stop",
        )

    async def stream_chat(self, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
        if self.delay:
            await asyncio.sleep(self.delay)
        yield LLMChunk(delta_reasoning="先思考一下。")
        yield LLMChunk(delta_content=self.reply)
        yield LLMChunk(finish_reason="stop")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config() -> NexusConfig:
    """构造测试用配置：openai 带 key，anthropic 不带 key。"""
    return NexusConfig(
        providers={
            "openai": ProviderConfig(
                api_key="sk-test1234567890abcd",
                model="gpt-4o-mini",
                max_tokens=4096,
                context_window_tokens=128000,
            ),
            "anthropic": ProviderConfig(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                context_window_tokens=200000,
            ),
        },
        default_provider="openai",
    )


@pytest.fixture
def server_env(tmp_path):
    """组装测试环境：app（注入 mock llm / tmp 会话目录 / tmp 配置路径）。"""
    config = _make_config()
    config.tools.enabled = ["read_file"]
    llm = MockLLM()
    mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
    save_path = str(tmp_path / "nexus.yaml")
    app = create_app(
        work_dir=str(tmp_path),
        config=config,
        llm=llm,
        session_manager=mgr,
        config_save_path=save_path,
    )
    return {
        "app": app,
        "config": config,
        "llm": llm,
        "mgr": mgr,
        "save_path": save_path,
    }


def _collect_until_done(ws, max_messages: int = 50) -> list[dict]:
    """持续接收 WS 消息直到 done / error，返回全部消息列表。"""
    events: list[dict] = []
    for _ in range(max_messages):
        evt = ws.receive_json()
        events.append(evt)
        if evt["type"] in ("done", "error"):
            break
    return events


# ---------------------------------------------------------------------------
# REST API 测试
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health(self, server_env):
        client = TestClient(server_env["app"])
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"ok": True, "provider": "openai", "model": "gpt-4o-mini"}


class TestConfigAPI:
    def test_get_config_structure_and_masking(self, server_env):
        """GET /api/config：结构完整，api_key 脱敏且不泄露明文。"""
        client = TestClient(server_env["app"])
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()

        # 顶层结构
        assert set(data.keys()) >= {
            "providers", "default_provider", "agent", "tools", "stream",
        }
        assert data["default_provider"] == "openai"
        assert data["agent"]["max_steps"] == 30
        assert data["tools"]["enabled"] == ["read_file"]
        assert data["stream"] is True

        # 带 key 的 provider：掩码保留后 4 位，不暴露完整 key
        openai = data["providers"]["openai"]
        assert openai["has_api_key"] is True
        assert openai["api_key"] is not None
        assert openai["api_key"].endswith("abcd")
        assert "****" in openai["api_key"]
        assert "sk-test1234567890abcd" not in json.dumps(data)

        # 不带 key 的 provider
        anthropic = data["providers"]["anthropic"]
        assert anthropic["has_api_key"] is False
        assert anthropic["api_key"] is None

    def test_put_config_updates_model_and_persists(self, server_env):
        """PUT /api/config：掩码回传不改 key，model 更新并写入 tmp 配置文件。"""
        app = server_env["app"]
        config = server_env["config"]
        client = TestClient(app)

        # 先 GET 拿到掩码
        masked = client.get("/api/config").json()["providers"]["openai"]["api_key"]

        # 掩码回传 + 更新 model
        resp = client.put(
            "/api/config",
            json={"providers": {"openai": {"api_key": masked, "model": "gpt-4o"}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["providers"]["openai"]["model"] == "gpt-4o"
        # 响应仍然脱敏
        assert "sk-test1234567890abcd" not in json.dumps(data)

        # 掩码回传不应覆盖原 key
        assert config.providers["openai"].api_key == "sk-test1234567890abcd"

        # GET 生效
        got = client.get("/api/config").json()
        assert got["providers"]["openai"]["model"] == "gpt-4o"

        # 持久化到 tmp_path 下的配置文件（不污染真实 ~/.nexus）
        with open(server_env["save_path"], "r", encoding="utf-8") as f:
            saved = f.read()
        assert "gpt-4o" in saved

    def test_put_config_with_new_api_key(self, server_env):
        """PUT /api/config：传新 api_key 则更新，响应中为新 key 的掩码。"""
        app = server_env["app"]
        config = server_env["config"]
        client = TestClient(app)

        resp = client.put(
            "/api/config",
            json={"providers": {"openai": {"api_key": "sk-newkey0000wxyz"}}},
        )
        assert resp.status_code == 200
        assert config.providers["openai"].api_key == "sk-newkey0000wxyz"
        masked = resp.json()["providers"]["openai"]["api_key"]
        assert masked.endswith("wxyz")
        assert "sk-newkey0000wxyz" not in json.dumps(resp.json())


class TestSessionsAPI:
    def _seed_session(self, mgr: SessionManager, session_id: str) -> None:
        state = AgentState(
            task="测试任务",
            messages=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，我是 Nexus。"},
            ],
        )
        mgr.save(state, session_id=session_id)

    def test_list_get_delete_sessions(self, server_env):
        mgr = server_env["mgr"]
        client = TestClient(server_env["app"])
        self._seed_session(mgr, "test0001")

        # 列表
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        sessions = resp.json()
        assert any(s["id"] == "test0001" for s in sessions)

        # 详情：元信息 + messages
        resp = client.get("/api/sessions/test0001")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["id"] == "test0001"
        assert detail["summary"] == "你好"
        assert len(detail["messages"]) == 2
        assert detail["messages"][0]["role"] == "user"

        # 删除
        resp = client.delete("/api/sessions/test0001")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # 删除后详情 404
        resp = client.get("/api/sessions/test0001")
        assert resp.status_code == 404

        # 删除不存在的会话
        resp = client.delete("/api/sessions/test0001")
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}


class TestToolsAPI:
    def test_list_tools(self, server_env):
        """GET /api/tools：返回 config.tools.enabled 指定的已注册工具。"""
        client = TestClient(server_env["app"])
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        tools = resp.json()
        names = [t["name"] for t in tools]
        assert names == ["read_file"]
        for t in tools:
            assert set(t.keys()) >= {"name", "description", "schema"}


# ---------------------------------------------------------------------------
# WebSocket /ws/chat 测试
# ---------------------------------------------------------------------------


class TestWsChat:
    def test_restore_message_multi_turn_and_reset(self, server_env):
        """完整流程：restore → message（delta → done）→ 多轮历史 → reset。"""
        llm = server_env["llm"]
        client = TestClient(server_env["app"])

        with client.websocket_connect("/ws/chat") as ws:
            # 1. restore 恢复历史会话
            ws.send_json(
                {
                    "type": "restore",
                    "messages": [
                        {"role": "user", "content": "历史问题"},
                        {"role": "assistant", "content": "历史回答"},
                    ],
                }
            )
            resp = ws.receive_json()
            assert resp == {"type": "restore_ok", "message_count": 2}

            # 2. 发送第一条消息：按序收到 thinking_delta → content_delta → done
            ws.send_json({"type": "message", "content": "你好"})
            events = _collect_until_done(ws)
            types = [e["type"] for e in events]
            assert "thinking_delta" in types
            assert "content_delta" in types
            assert types.index("thinking_delta") < types.index("content_delta")
            assert events[-1]["type"] == "done"
            assert "steps" in events[-1]
            assert set(events[-1]["usage"].keys()) == {
                "prompt_tokens", "completion_tokens", "total_tokens",
            }

            thinking = "".join(
                e["delta"] for e in events if e["type"] == "thinking_delta"
            )
            content = "".join(
                e["delta"] for e in events if e["type"] == "content_delta"
            )
            assert thinking == "先思考一下。"
            assert content == llm.reply

            # 3. LLM 收到的 messages 应包含 restore 的历史
            first_call_text = json.dumps(llm.calls[-1], ensure_ascii=False)
            assert "历史问题" in first_call_text
            assert "你好" in first_call_text

            # 4. 第二轮：多轮历史保持（第一轮的用户消息仍在上下文中）
            ws.send_json({"type": "message", "content": "第二条"})
            events = _collect_until_done(ws)
            assert events[-1]["type"] == "done"
            second_call_text = json.dumps(llm.calls[-1], ensure_ascii=False)
            assert "历史问题" in second_call_text
            assert "你好" in second_call_text
            assert "第二条" in second_call_text

            # 5. reset 清空历史
            ws.send_json({"type": "reset"})
            assert ws.receive_json() == {"type": "reset_ok"}

            ws.send_json({"type": "message", "content": "全新开始"})
            events = _collect_until_done(ws)
            assert events[-1]["type"] == "done"
            third_call_text = json.dumps(llm.calls[-1], ensure_ascii=False)
            assert "全新开始" in third_call_text
            # reset 后历史不应再出现
            assert "历史问题" not in third_call_text
            assert "第二条" not in third_call_text

    def test_concurrent_message_rejected(self, tmp_path):
        """run 进行中再发 message，收到「上一个任务仍在执行中」错误。"""
        config = _make_config()
        llm = MockLLM(delay=0.5)  # 延迟模拟长任务
        mgr = SessionManager(sessions_dir=str(tmp_path / "sessions"))
        app = create_app(
            work_dir=str(tmp_path),
            config=config,
            llm=llm,
            session_manager=mgr,
            config_save_path=str(tmp_path / "nexus.yaml"),
        )
        client = TestClient(app)

        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "message", "content": "任务一"})
            # 第一个 run 尚未完成，立刻再发一条
            ws.send_json({"type": "message", "content": "任务二"})

            # 由于 mock 延迟 0.5s 才产出首个 chunk，
            # 第一条收到的应是并发拒绝错误
            first = ws.receive_json()
            assert first["type"] == "error"
            assert "上一个任务仍在执行中" in first["message"]

            # 随后第一个 run 正常完成
            events = _collect_until_done(ws)
            assert events[-1]["type"] == "done"

            # 任务二被拒绝，LLM 只被调用了一次
            assert len(llm.calls) == 1

    def test_session_persisted_after_run(self, server_env):
        """WS 对话完成后持久化到 SessionManager，done 事件携带 session_id。"""
        mgr = server_env["mgr"]
        llm = server_env["llm"]
        client = TestClient(server_env["app"])

        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "message", "content": "你好"})
            events = _collect_until_done(ws)
            done = events[-1]
            assert done["type"] == "done"

            # done 事件携带 8 位 session_id
            session_id = done.get("session_id")
            assert isinstance(session_id, str)
            assert len(session_id) == 8

            # 会话文件已写入 tmp 会话目录，且出现在历史会话列表中
            sessions = mgr.list_sessions()
            assert [s["id"] for s in sessions] == [session_id]

            # 持久化的 messages 与实际对话一致（user + assistant）
            state = mgr.load(session_id)
            assert state is not None
            assert [m["role"] for m in state.messages] == ["user", "assistant"]
            assert state.messages[0]["content"] == "你好"
            assert state.messages[1]["content"] == llm.reply

            # metadata 标记为 desktop 模式
            with open(
                mgr.sessions_dir / f"{session_id}.json", "r", encoding="utf-8"
            ) as f:
                raw = json.load(f)
            assert raw["metadata"]["mode"] == "desktop"

    def test_second_run_overwrites_same_session(self, server_env):
        """同一连接第二次 run 覆盖同一会话文件，不产生新文件。"""
        mgr = server_env["mgr"]
        client = TestClient(server_env["app"])

        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"type": "message", "content": "第一条"})
            done1 = _collect_until_done(ws)[-1]
            assert done1["type"] == "done"

            ws.send_json({"type": "message", "content": "第二条"})
            done2 = _collect_until_done(ws)[-1]
            assert done2["type"] == "done"

            # 同一连接共用同一个 session_id
            assert done1["session_id"] == done2["session_id"]

            # 仍然只有一个会话文件（覆盖式保存）
            files = list(mgr.sessions_dir.glob("*.json"))
            assert len(files) == 1
            assert files[0].stem == done1["session_id"]

            # 文件中包含两轮完整对话
            state = mgr.load(done1["session_id"])
            assert state is not None
            user_msgs = [
                m["content"] for m in state.messages if m["role"] == "user"
            ]
            assert user_msgs == ["第一条", "第二条"]


# ---------------------------------------------------------------------------
# MCP /api/mcp 契约测试
# ---------------------------------------------------------------------------


@pytest.fixture
def no_mcp_connect(monkeypatch):
    """拦截 MCP 插件获取/安装路径，避免测试触发真实 MCP 连接（npx 进程等）。

    所有 /api/mcp 热连接路径（_reload_mcp / _mcp_status_items / reconnect）
    都经 _get_mcp_plugin 获取插件，替换为恒返回 None 即可全部截断。
    """

    async def _no_plugin(app):
        return None

    monkeypatch.setattr("nexus.server.app._get_mcp_plugin", _no_plugin)


class TestMcpApi:
    """/api/mcp 五个端点 + GET /api/tools origin/server 字段的契约测试。"""

    def test_get_mcp_empty(self, server_env, no_mcp_connect):
        """GET /api/mcp：空配置返回空列表。"""
        client = TestClient(server_env["app"])
        resp = client.get("/api/mcp")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_post_mcp_validation(self, server_env, no_mcp_connect):
        """POST /api/mcp：缺 name、缺 command 与 url、非 JSON 均 400。"""
        client = TestClient(server_env["app"])
        # 缺 name
        resp = client.post("/api/mcp", json={"command": "npx"})
        assert resp.status_code == 400
        assert "name" in resp.json()["detail"]
        # command 与 url 都缺
        resp = client.post("/api/mcp", json={"name": "fs"})
        assert resp.status_code == 400
        # 请求体不是合法 JSON
        resp = client.post(
            "/api/mcp", content="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_post_mcp_stdio(self, server_env, no_mcp_connect):
        """POST /api/mcp 合法 stdio 配置 → 200，随后 GET 包含该 server。"""
        client = TestClient(server_env["app"])
        resp = client.post(
            "/api/mcp",
            json={"name": "fs", "command": "npx", "args": ["-y", "pkg"]},
        )
        assert resp.status_code == 200
        item = resp.json()
        assert item["name"] == "fs"
        assert item["transport"] == "stdio"
        # 热连接被拦截、无真实插件时按配置推断为 disconnected
        assert item["status"] == "disconnected"
        assert item["tool_count"] == 0

        got = client.get("/api/mcp").json()
        assert [s["name"] for s in got] == ["fs"]
        assert got[0]["command"] == "npx"

    def test_put_mcp_update_and_404(self, server_env, no_mcp_connect):
        """PUT /api/mcp/{name}：enabled=false 反映到 GET；不存在 → 404。"""
        client = TestClient(server_env["app"])
        client.post("/api/mcp", json={"name": "fs", "command": "npx"})

        resp = client.put("/api/mcp/fs", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        got = client.get("/api/mcp").json()
        assert got[0]["enabled"] is False

        resp = client.put("/api/mcp/ghost", json={"enabled": False})
        assert resp.status_code == 404

    def test_delete_mcp(self, server_env, no_mcp_connect):
        """DELETE /api/mcp/{name}：存在 → ok；不存在 → 404。"""
        client = TestClient(server_env["app"])
        client.post("/api/mcp", json={"name": "fs", "command": "npx"})

        resp = client.delete("/api/mcp/fs")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert client.get("/api/mcp").json() == []

        resp = client.delete("/api/mcp/fs")
        assert resp.status_code == 404

    def test_reconnect_mcp(self, server_env, no_mcp_connect):
        """POST /api/mcp/{name}/reconnect：不存在 → 404；存在但无插件 → 400。"""
        client = TestClient(server_env["app"])
        resp = client.post("/api/mcp/ghost/reconnect")
        assert resp.status_code == 404

        client.post("/api/mcp", json={"name": "fs", "command": "npx"})
        resp = client.post("/api/mcp/fs/reconnect")
        assert resp.status_code == 400
        assert "MCP 未启用" in resp.json()["detail"]

    def test_tools_origin_builtin(self, server_env, no_mcp_connect):
        """GET /api/tools：内置工具 origin=builtin、server=None。"""
        client = TestClient(server_env["app"])
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        tools = resp.json()
        assert tools
        for t in tools:
            assert t["origin"] == "builtin"
            assert t["server"] is None
