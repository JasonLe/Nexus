"""ToolExecutor 超时控制测试。"""

import asyncio

import pytest

from nexus.tools.base import BaseTool, ToolResult
from nexus.tools.executor import DEFAULT_TOOL_TIMEOUT, ToolExecutor
from nexus.tools.registry import ToolRegistry


class _SlowTool(BaseTool):
    """模拟慢速工具，可控制执行耗时。"""

    def __init__(self, delay: float, name: str = "slow_tool"):
        self._name = name
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "A slow tool for testing"

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict) -> ToolResult:
        await asyncio.sleep(self._delay)
        return ToolResult.ok(data="done")


class _SlowToolWithCustomTimeout(_SlowTool):
    """自定义 timeout 属性的工具。"""

    @property
    def timeout(self) -> float | None:
        return 0.3


class _FastTool(BaseTool):
    """快速工具。"""

    @property
    def name(self) -> str:
        return "fast_tool"

    @property
    def description(self) -> str:
        return "A fast tool"

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict) -> ToolResult:
        return ToolResult.ok(data="fast")


class TestToolExecutorTimeout:
    """ToolExecutor 超时控制测试。"""

    @pytest.mark.asyncio
    async def test_fast_tool_completes(self):
        """快速工具正常完成。"""
        registry = ToolRegistry()
        registry.register(_FastTool())
        executor = ToolExecutor(registry, default_timeout=5.0)

        result = await executor.execute("fast_tool", "call_1", {})
        assert result.success
        assert result.data == "fast"

    @pytest.mark.asyncio
    async def test_slow_tool_times_out(self):
        """慢速工具超时后返回失败。"""
        registry = ToolRegistry()
        registry.register(_SlowTool(delay=2.0))
        executor = ToolExecutor(registry, default_timeout=0.2)

        result = await executor.execute("slow_tool", "call_1", {})

        assert not result.success
        assert "timed out" in result.error
        assert "0.2s" in result.error

    @pytest.mark.asyncio
    async def test_tool_timeout_overrides_default(self):
        """工具自身的 timeout 属性优先于 executor.default_timeout。"""
        registry = ToolRegistry()
        # 工具自定义 timeout=0.3s，但 executor 默认 5s
        registry.register(_SlowToolWithCustomTimeout(delay=1.0, name="custom_timeout"))
        executor = ToolExecutor(registry, default_timeout=5.0)

        result = await executor.execute("custom_timeout", "call_1", {})

        # 应按工具的 0.3s 超时，而非 executor 的 5s
        assert not result.success
        assert "0.3s" in result.error

    @pytest.mark.asyncio
    async def test_default_timeout_none_no_timeout(self):
        """default_timeout=None 时不强制超时。"""
        registry = ToolRegistry()
        registry.register(_SlowTool(delay=0.1))
        executor = ToolExecutor(registry, default_timeout=None)

        result = await executor.execute("slow_tool", "call_1", {})
        assert result.success
        assert result.data == "done"

    @pytest.mark.asyncio
    async def test_default_timeout_constant(self):
        """DEFAULT_TOOL_TIMEOUT 常量存在且为正数。"""
        assert DEFAULT_TOOL_TIMEOUT > 0
        assert DEFAULT_TOOL_TIMEOUT == 30.0

    @pytest.mark.asyncio
    async def test_timeout_returns_tool_result_not_exception(self):
        """超时返回 ToolResult.fail 而非抛出异常。"""
        registry = ToolRegistry()
        registry.register(_SlowTool(delay=5.0))
        executor = ToolExecutor(registry, default_timeout=0.1)

        # 不应抛出异常
        result = await executor.execute("slow_tool", "call_1", {})
        assert isinstance(result, ToolResult)
        assert not result.success
        assert result.tool_name == "slow_tool"

    def test_base_tool_default_timeout_none(self):
        """BaseTool 默认 timeout 为 None。"""
        tool = _FastTool()
        assert tool.timeout is None
