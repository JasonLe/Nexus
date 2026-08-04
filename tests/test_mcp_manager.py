"""测试 MCP 管理器：MCPManager 批量连接、注册、故障隔离与断开。

覆盖范围
--------
- connect_all：enabled server 全部建连、工具以 mcp__ 前缀注册、状态反映
- disabled server 状态为 disabled 且不建连
- 故障隔离：单个 server 连接失败不影响其余 server
- disconnect_all 幂等：注销 mcp__ 工具、关闭连接、重复调用不报错
- reconnect 成功 / 未配置 / 失败三种路径
- remove 断开连接并移除状态与配置
- is_connected 状态查询

说明：manager 模块内 ``MCPClient`` 为模块级 import，测试通过 monkeypatch
替换为 FakeMCPClient，不依赖真实 MCP server / npx 进程。
"""

from types import SimpleNamespace

import pytest

from nexus.cli.config import MCPServerConfig
from nexus.tools.mcp import manager as mcp_manager
from nexus.tools.mcp.manager import MCPManager
from nexus.tools.registry import ToolRegistry

# 固定的远端工具列表：FakeMCPClient.list_tools 返回其副本
_REMOTE_TOOLS = [
    {"name": "read", "description": "读取文件", "schema": {"type": "object", "properties": {}}},
    {"name": "write", "description": "写入文件", "schema": {"type": "object", "properties": {}}},
]


class FakeMCPClient:
    """替换 MCPClient 的假客户端：记录构造参数与调用，可配置 connect 失败。

    类属性 ``connect_error`` 非 None 时，之后创建的实例 connect() 抛该异常；
    ``instances`` 记录全部构造的实例，供断言「未对 disabled/失败 server 建连」。
    """

    instances: list["FakeMCPClient"] = []
    connect_error: Exception | None = None

    def __init__(
        self,
        *,
        name: str = "",
        command: str | None = None,
        args: list | None = None,
        env: dict | None = None,
        url: str | None = None,
        connect_timeout: float = 30.0,
    ):
        self.name = name
        self.command = command
        self.args = list(args) if args else []
        self.env = dict(env) if env else {}
        self.url = url
        self.connect_called = False
        self.close_called = False
        self.call_calls: list[tuple] = []
        FakeMCPClient.instances.append(self)

    @property
    def transport(self) -> str:
        """与真实 MCPClient 相同的传输判定。"""
        return "http" if self.url else "stdio"

    async def connect(self) -> None:
        self.connect_called = True
        if FakeMCPClient.connect_error is not None:
            raise FakeMCPClient.connect_error

    async def list_tools(self) -> list[dict]:
        return [dict(t) for t in _REMOTE_TOOLS]

    async def call_tool(self, name: str, arguments: dict | None = None) -> SimpleNamespace:
        self.call_calls.append((name, arguments))
        return SimpleNamespace(structured_content={"ok": True}, content=[], is_error=False)

    async def close(self) -> None:
        self.close_called = True


@pytest.fixture
def manager_env(monkeypatch):
    """替换 manager 模块的 MCPClient 为 FakeMCPClient，返回 (manager, registry)。"""
    FakeMCPClient.instances.clear()
    FakeMCPClient.connect_error = None
    monkeypatch.setattr(mcp_manager, "MCPClient", FakeMCPClient)
    return MCPManager(), ToolRegistry()


def _configs() -> dict[str, MCPServerConfig]:
    """两个 enabled（stdio / http）+ 一个 disabled 的测试配置。"""
    return {
        "fs": MCPServerConfig(command="npx", args=["-y", "pkg"], env={"K": "v"}),
        "http": MCPServerConfig(url="http://localhost:3000/mcp"),
        "off": MCPServerConfig(command="npx", enabled=False),
    }


# ---------------------------------------------------------------------------
# connect_all
# ---------------------------------------------------------------------------


class TestConnectAll:
    """测试批量连接与工具注册。"""

    @pytest.mark.asyncio
    async def test_enabled_servers_connected_and_registered(self, manager_env):
        """enabled server 全部建连、工具以 mcp__ 前缀注册、状态反映。"""
        manager, registry = manager_env
        await manager.connect_all(_configs(), registry)

        # 只为两个 enabled server 建连（disabled 不建连）
        assert len(FakeMCPClient.instances) == 2
        assert {c.name for c in FakeMCPClient.instances} == {"fs", "http"}
        assert all(c.connect_called for c in FakeMCPClient.instances)
        assert manager.is_connected("fs")
        assert manager.is_connected("http")
        assert not manager.is_connected("off")

        # 工具以 mcp__{server}__{tool} 注册进真实 ToolRegistry
        names = {t.name for t in registry.list()}
        assert names == {
            "mcp__fs__read", "mcp__fs__write",
            "mcp__http__read", "mcp__http__write",
        }
        adapter = registry.get("mcp__fs__read")
        assert adapter is not None
        assert adapter.description.startswith("[MCP: fs]")

        # get_status 反映 connected 状态与工具数量
        status = {s["name"]: s for s in manager.get_status()}
        assert status["fs"]["status"] == "connected"
        assert status["fs"]["tool_count"] == 2
        assert status["http"]["status"] == "connected"
        assert status["http"]["tool_count"] == 2

    @pytest.mark.asyncio
    async def test_disabled_server_status(self, manager_env):
        """disabled server 状态为 disabled 且不建连、不注册工具。"""
        manager, registry = manager_env
        await manager.connect_all(_configs(), registry)

        assert all(c.name != "off" for c in FakeMCPClient.instances)
        status = {s["name"]: s for s in manager.get_status()}
        assert status["off"]["status"] == "disabled"
        assert status["off"]["tool_count"] == 0
        assert status["off"]["transport"] == "stdio"


# ---------------------------------------------------------------------------
# 故障隔离
# ---------------------------------------------------------------------------


class TestFaultIsolation:
    """测试单个 server 连接失败不影响其余 server。"""

    @pytest.mark.asyncio
    async def test_partial_failure(self, manager_env, monkeypatch):
        """fs 连接失败 → 状态 error、工具不注册；http 正常连接并注册。"""
        manager, registry = manager_env

        orig_connect = FakeMCPClient.connect

        async def fail_fs_connect(self):
            if self.name == "fs":
                raise RuntimeError("fs server down")
            await orig_connect(self)

        monkeypatch.setattr(FakeMCPClient, "connect", fail_fs_connect)
        await manager.connect_all(_configs(), registry)

        status = {s["name"]: s for s in manager.get_status()}
        assert status["fs"]["status"] == "error"
        assert "fs server down" in status["fs"]["error"]
        assert not manager.is_connected("fs")

        # 其余 server 不受影响
        assert manager.is_connected("http")
        assert status["http"]["status"] == "connected"
        assert {t.name for t in registry.list()} == {
            "mcp__http__read", "mcp__http__write",
        }

    @pytest.mark.asyncio
    async def test_all_fail_then_recover(self, manager_env):
        """全部连接失败时无工具注册；恢复后可正常连接。"""
        manager, registry = manager_env

        FakeMCPClient.connect_error = RuntimeError("boom")
        await manager.connect_all(_configs(), registry)
        assert registry.list() == []
        status = {s["name"]: s for s in manager.get_status()}
        assert status["fs"]["status"] == "error"
        assert "boom" in status["fs"]["error"]

        # 恢复后新配置可正常连接
        FakeMCPClient.connect_error = None
        await manager.connect_all({"only": MCPServerConfig(command="npx")}, registry)
        status = {s["name"]: s for s in manager.get_status()}
        assert status["only"]["status"] == "connected"
        assert {t.name for t in registry.list()} == {"mcp__only__read", "mcp__only__write"}


# ---------------------------------------------------------------------------
# disconnect_all
# ---------------------------------------------------------------------------


class TestDisconnectAll:
    """测试批量断开（注销工具 + 关闭连接）的幂等性。"""

    @pytest.mark.asyncio
    async def test_disconnect_all_idempotent(self, manager_env):
        """disconnect_all 注销全部 mcp__ 工具并关闭连接，重复调用不报错。"""
        manager, registry = manager_env
        await manager.connect_all(_configs(), registry)
        assert len(registry.list()) == 4

        await manager.disconnect_all(registry)
        assert registry.list() == []
        assert all(c.close_called for c in FakeMCPClient.instances)
        assert not manager.is_connected("fs")
        status = {s["name"]: s for s in manager.get_status()}
        assert status["fs"]["status"] == "disconnected"

        # 幂等：再次断开不报错，工具保持注销、连接不重复 close
        await manager.disconnect_all(registry)
        assert registry.list() == []
        assert not manager.is_connected("http")


# ---------------------------------------------------------------------------
# reconnect
# ---------------------------------------------------------------------------


class TestReconnect:
    """测试重连的三种路径。"""

    @pytest.mark.asyncio
    async def test_reconnect_success(self, manager_env):
        """reconnect 成功返回 True 并重新注册工具。"""
        manager, registry = manager_env
        await manager.connect_all({"fs": MCPServerConfig(command="npx")}, registry)
        await manager.disconnect_all(registry)
        assert registry.list() == []

        ok = await manager.reconnect("fs", registry)
        assert ok is True
        assert manager.is_connected("fs")
        assert {t.name for t in registry.list()} == {"mcp__fs__read", "mcp__fs__write"}

    @pytest.mark.asyncio
    async def test_reconnect_unknown_name(self, manager_env):
        """未配置的 server 重连返回 False。"""
        manager, registry = manager_env
        ok = await manager.reconnect("ghost", registry)
        assert ok is False

    @pytest.mark.asyncio
    async def test_reconnect_failure(self, manager_env):
        """重连时连接失败返回 False，状态为 error。"""
        manager, registry = manager_env
        await manager.connect_all({"fs": MCPServerConfig(command="npx")}, registry)
        await manager.disconnect_all(registry)

        FakeMCPClient.connect_error = RuntimeError("boom")
        ok = await manager.reconnect("fs", registry)
        assert ok is False
        assert not manager.is_connected("fs")
        assert registry.list() == []
        status = {s["name"]: s for s in manager.get_status()}
        assert status["fs"]["status"] == "error"


# ---------------------------------------------------------------------------
# remove / is_connected
# ---------------------------------------------------------------------------


class TestRemoveAndIsConnected:
    """测试 remove 与 is_connected。"""

    @pytest.mark.asyncio
    async def test_remove_disconnects_and_removes(self, manager_env):
        """remove 断开连接、注销工具并移除状态与配置。"""
        manager, registry = manager_env
        await manager.connect_all({"fs": MCPServerConfig(command="npx")}, registry)
        assert manager.is_connected("fs")
        assert len(registry.list()) == 2

        await manager.remove("fs", registry)
        assert not manager.is_connected("fs")
        assert registry.list() == []
        assert manager.get_status() == []
        # 配置已移除 → 重连返回 False
        assert await manager.reconnect("fs", registry) is False

    @pytest.mark.asyncio
    async def test_remove_unknown_name(self, manager_env):
        """remove 不存在的 server 不报错。"""
        manager, registry = manager_env
        await manager.remove("ghost", registry)  # 不应抛异常
        assert manager.get_status() == []

    @pytest.mark.asyncio
    async def test_is_connected(self, manager_env):
        """is_connected 正确反映各阶段连接状态。"""
        manager, registry = manager_env
        assert manager.is_connected("fs") is False
        assert manager.is_connected("ghost") is False

        await manager.connect_all({"fs": MCPServerConfig(command="npx")}, registry)
        assert manager.is_connected("fs") is True

        await manager.disconnect_all(registry)
        assert manager.is_connected("fs") is False
