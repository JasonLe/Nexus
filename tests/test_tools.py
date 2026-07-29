"""测试工具系统：BaseTool、ToolRegistry、ToolExecutor。

覆盖范围
--------
- ToolRegistry 注册、按名称获取、列出所有工具
- ToolRegistry 重复注册同名工具抛出 ValueError
- ToolRegistry 导出 OpenAI 格式 schemas
- ToolExecutor 执行成功
- ToolExecutor 工具未注册时返回失败结果
- ToolExecutor 参数校验失败时返回失败结果
- ToolResult 工厂方法 ok() 和 fail()
- BaseTool.to_openai_schema() 输出格式正确
"""

import pytest
from nexus.tools.base import BaseTool, ToolResult
from nexus.tools.registry import ToolRegistry
from nexus.tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# Mock 工具定义
# ---------------------------------------------------------------------------


class MockTool(BaseTool):
    """基础 Mock 工具 —— 无参数，始终成功。"""

    name = "mock"
    description = "A mock tool for testing"
    schema: dict = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args):
        return ToolResult(success=True, data=args, tool_name=self.name)


class MockToolWithParams(BaseTool):
    """带参数的 Mock 工具 —— 需要 query 参数。"""

    name = "search"
    description = "Search the web for information"
    schema: dict = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keyword"},
            "max_results": {"type": "integer", "description": "Max results", "default": 10},
        },
        "required": ["query"],
    }

    async def execute(self, args):
        return ToolResult(success=True, data={"results": [f"Result for: {args['query']}"]}, tool_name=self.name)


class FailingTool(BaseTool):
    """始终返回失败的 Mock 工具。"""

    name = "failing"
    description = "A tool that always fails"
    schema: dict = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args):
        return ToolResult.fail(error="This tool always fails", tool_name=self.name)


class CrashingTool(BaseTool):
    """execute 中抛出异常的工具。"""

    name = "crasher"
    description = "A tool that crashes during execution"
    schema: dict = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args):
        raise RuntimeError("Boom!")


# ---------------------------------------------------------------------------
# ToolRegistry 测试
# ---------------------------------------------------------------------------


class TestToolRegistry:
    """测试 ToolRegistry 的注册与查询。"""

    def test_register_tool(self):
        """注册工具后，列表中应能找到该工具。"""
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)

        tools = registry.list()
        assert len(tools) == 1
        assert tools[0].name == "mock"

    def test_get_tool(self):
        """按名称获取工具应返回正确的工具实例。"""
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)

        retrieved = registry.get("mock")
        assert retrieved is tool
        assert retrieved.name == "mock"

    def test_get_nonexistent_tool(self):
        """获取未注册的工具应返回 None。"""
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_list_tools(self):
        """列出所有工具应返回全部已注册工具。"""
        registry = ToolRegistry()
        tool_a = MockTool()
        tool_b = MockToolWithParams()
        registry.register(tool_a)
        registry.register(tool_b)

        tools = registry.list()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"mock", "search"}

    def test_duplicate_register(self):
        """重复注册同名工具应抛出 ValueError。"""
        registry = ToolRegistry()
        tool1 = MockTool()
        tool2 = MockTool()  # 同名但不同实例

        registry.register(tool1)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool2)

    def test_unregister(self):
        """注销工具后，列表中不再包含该工具。"""
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)

        removed = registry.unregister("mock")
        assert removed is tool
        assert registry.get("mock") is None
        assert len(registry.list()) == 0

    def test_unregister_nonexistent(self):
        """注销不存在的工具应返回 None，不报错。"""
        registry = ToolRegistry()
        assert registry.unregister("nonexistent") is None

    def test_contains(self):
        """支持 'name' in registry 语法。"""
        registry = ToolRegistry()
        tool = MockTool()
        registry.register(tool)

        assert "mock" in registry
        assert "nonexistent" not in registry

    def test_to_openai_schemas(self):
        """导出 OpenAI 格式 schemas 应包含所有工具的正确格式。"""
        registry = ToolRegistry()
        registry.register(MockTool())
        registry.register(MockToolWithParams())

        schemas = registry.to_openai_schemas()
        assert len(schemas) == 2

        for schema in schemas:
            assert "type" in schema
            assert schema["type"] == "function"
            assert "function" in schema
            assert "name" in schema["function"]
            assert "description" in schema["function"]
            assert "parameters" in schema["function"]

    def test_iter_protocol(self):
        """registry 支持 for tool in registry 迭代。"""
        registry = ToolRegistry()
        registry.register(MockTool())
        registry.register(MockToolWithParams())

        names = []
        for tool in registry:
            names.append(tool.name)
        assert sorted(names) == ["mock", "search"]

    def test_len(self):
        """len(registry) 应返回已注册工具数量。"""
        registry = ToolRegistry()
        assert len(registry) == 0

        registry.register(MockTool())
        assert len(registry) == 1

        registry.register(MockToolWithParams())
        assert len(registry) == 2


# ---------------------------------------------------------------------------
# ToolExecutor 测试
# ---------------------------------------------------------------------------


class TestToolExecutor:
    """测试 ToolExecutor 执行器。"""

    @pytest.mark.asyncio
    async def test_executor_execute_success(self):
        """ToolExecutor 执行成功时应返回 success=True 的 ToolResult。"""
        registry = ToolRegistry()
        registry.register(MockTool())
        executor = ToolExecutor(registry)

        result = await executor.execute(
            tool_name="mock",
            tool_call_id="call_001",
            arguments={},
        )

        assert result.success is True
        assert result.data == {}
        assert result.tool_name == "mock"
        assert result.error is None
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_executor_with_params(self):
        """带参数的工具执行应正确传递参数。"""
        registry = ToolRegistry()
        registry.register(MockToolWithParams())
        executor = ToolExecutor(registry)

        result = await executor.execute(
            tool_name="search",
            tool_call_id="call_002",
            arguments={"query": "python testing"},
        )

        assert result.success is True
        assert "Result for: python testing" in result.data["results"][0]

    @pytest.mark.asyncio
    async def test_executor_tool_not_found(self):
        """工具未注册时应返回 success=False 的 ToolResult。"""
        registry = ToolRegistry()
        executor = ToolExecutor(registry)

        result = await executor.execute(
            tool_name="nonexistent",
            tool_call_id="call_003",
            arguments={},
        )

        assert result.success is False
        assert result.error is not None
        assert "not registered" in result.error

    @pytest.mark.asyncio
    async def test_executor_validation_error_missing_required(self):
        """缺少 required 参数时应返回校验失败。"""
        registry = ToolRegistry()
        registry.register(MockToolWithParams())
        executor = ToolExecutor(registry)

        # MockToolWithParams 要求 "query" 参数
        result = await executor.execute(
            tool_name="search",
            tool_call_id="call_004",
            arguments={},  # 缺少 query
        )

        assert result.success is False
        assert result.error is not None
        assert "Missing required parameter" in result.error
        assert "query" in result.error

    @pytest.mark.asyncio
    async def test_executor_validation_error_type_mismatch(self):
        """参数类型不匹配时应返回校验失败。"""
        registry = ToolRegistry()
        registry.register(MockToolWithParams())
        executor = ToolExecutor(registry)

        # query 是 string 类型，传 integer 应失败
        result = await executor.execute(
            tool_name="search",
            tool_call_id="call_005",
            arguments={"query": 123},  # 类型错误
        )

        assert result.success is False
        assert result.error is not None
        assert "Type mismatch" in result.error

    @pytest.mark.asyncio
    async def test_executor_tool_returns_failure(self):
        """工具自身返回失败时，executor 应正确传递失败结果。"""
        registry = ToolRegistry()
        registry.register(FailingTool())
        executor = ToolExecutor(registry)

        result = await executor.execute(
            tool_name="failing",
            tool_call_id="call_006",
            arguments={},
        )

        assert result.success is False
        assert result.error == "This tool always fails"
        assert result.tool_name == "failing"

    @pytest.mark.asyncio
    async def test_executor_tool_raises_exception(self):
        """工具 execute 中抛出异常时，executor 应捕获并返回失败结果。"""
        registry = ToolRegistry()
        registry.register(CrashingTool())
        executor = ToolExecutor(registry)

        result = await executor.execute(
            tool_name="crasher",
            tool_call_id="call_007",
            arguments={},
        )

        assert result.success is False
        assert result.error is not None
        assert "Tool execution error" in result.error
        assert "Boom!" in result.error


# ---------------------------------------------------------------------------
# ToolResult 工厂方法测试
# ---------------------------------------------------------------------------


class TestToolResult:
    """测试 ToolResult 工厂方法。"""

    def test_ok_factory(self):
        """ToolResult.ok() 应创建 success=True 的结果。"""
        result = ToolResult.ok(data="hello", tool_name="test_tool", duration_ms=10.0)
        assert result.success is True
        assert result.data == "hello"
        assert result.tool_name == "test_tool"
        assert result.duration_ms == 10.0
        assert result.error is None

    def test_fail_factory(self):
        """ToolResult.fail() 应创建 success=False 的结果。"""
        result = ToolResult.fail(error="something went wrong", tool_name="test_tool", duration_ms=5.0)
        assert result.success is False
        assert result.error == "something went wrong"
        assert result.tool_name == "test_tool"
        assert result.duration_ms == 5.0
        assert result.data is None


# ---------------------------------------------------------------------------
# BaseTool.to_openai_schema 测试
# ---------------------------------------------------------------------------


class TestBaseToolSchema:
    """测试 BaseTool.to_openai_schema()。"""

    def test_to_openai_schema_format(self):
        """to_openai_schema() 应返回正确的 OpenAI 格式。"""
        tool = MockTool()
        schema = tool.to_openai_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "mock"
        assert schema["function"]["description"] == "A mock tool for testing"
        assert schema["function"]["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }
