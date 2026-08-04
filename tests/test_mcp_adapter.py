"""测试 MCP 工具适配层：MCPToolAdapter 的命名、schema、执行与错误转换。

覆盖范围
--------
- 本地工具名 ``mcp__{server}__{tool}`` 前缀，非法字符（-、空格、点）替换为 _
- 远端 inputSchema 透传；非法/缺失时回退为空 object schema
- description 包含来源标识 ``[MCP: server]``
- execute 成功：结构化数据 / 纯文本内容 / 混合内容块
- execute 失败：远端异常与 isError 均转为 ToolResult.fail（不抛未捕获异常）
- to_openai_schema() 输出格式正确

说明：本测试不依赖真实 MCP server / npx 进程，全部通过 FakeMCPClient
返回 ``SimpleNamespace`` 形式的 CallToolResult 模拟 SDK 返回值。
"""

from types import SimpleNamespace

import pytest

from nexus.tools.mcp.adapter import MCPToolAdapter, _sanitize


class FakeMCPClient:
    """适配层测试用的假 MCP 客户端，仅实现 adapter 依赖的 call_tool 接口。

    ``result`` 模拟 SDK 的 CallToolResult（对象形态，含 structured_content /
    content / is_error 等属性）；``error`` 非 None 时 call_tool 抛出该异常。
    """

    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict | None = None) -> object:
        self.calls.append((name, arguments))
        if self._error is not None:
            raise self._error
        return self._result


def _make_adapter(client=None, server_name: str = "fs", remote_tool: dict | None = None):
    """构造一个默认配置的 MCPToolAdapter，便于各用例定制。"""
    client = client or FakeMCPClient(
        result=SimpleNamespace(structured_content=None, content=[], is_error=False)
    )
    remote_tool = remote_tool or {
        "name": "read",
        "description": "读取文件内容",
        "schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    return MCPToolAdapter(client=client, server_name=server_name, remote_tool=remote_tool)


# ---------------------------------------------------------------------------
# 命名规范
# ---------------------------------------------------------------------------


class TestNaming:
    """测试本地工具名 mcp__{server}__{tool} 与非法字符清洗。"""

    def test_name_prefix(self):
        """本地工具名带 mcp__ 前缀。"""
        adapter = _make_adapter()
        assert adapter.name == "mcp__fs__read"

    def test_name_sanitizes_invalid_chars(self):
        """server/tool 名中的 -、空格、点 等非法字符被替换为 _。"""
        adapter = _make_adapter(
            server_name="my-server", remote_tool={"name": "read file.txt", "schema": None}
        )
        assert adapter.name == "mcp__my_server__read_file_txt"
        # 与底层 _sanitize 行为一致
        expected = f"mcp__{_sanitize('my-server')}__{_sanitize('read file.txt')}"
        assert adapter.name == expected
        # 工具名必须是 ^[a-zA-Z0-9_]+$ 可表示的
        assert all(c.isalnum() or c == "_" for c in adapter.name)

    def test_name_multi_invalid_chars(self):
        """连续非法字符每个单独替换为 _。"""
        assert _sanitize("a--b c.d") == "a__b_c_d"


# ---------------------------------------------------------------------------
# schema 透传与回退
# ---------------------------------------------------------------------------


class TestSchema:
    """测试远端 inputSchema 透传与回退。"""

    def test_schema_passthrough(self):
        """合法 object schema 原样透传。"""
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
        adapter = _make_adapter(remote_tool={"name": "read", "description": "d", "schema": schema})
        assert adapter.schema == schema

    def test_schema_object_without_type(self):
        """dict 缺 type 字段时视为 object，透传（与实现语义一致）。"""
        schema = {"properties": {"x": {"type": "string"}}}
        adapter = _make_adapter(remote_tool={"name": "read", "description": "d", "schema": schema})
        assert adapter.schema == schema

    def test_schema_fallback_non_object(self):
        """type 非 object 的 schema 回退为空 object schema。"""
        adapter = _make_adapter(
            remote_tool={"name": "read", "description": "d", "schema": {"type": "array"}}
        )
        assert adapter.schema == {"type": "object", "properties": {}}

    def test_schema_fallback_missing(self):
        """schema 缺失（None）时回退为空 object schema。"""
        adapter = _make_adapter(remote_tool={"name": "read", "description": "d", "schema": None})
        assert adapter.schema == {"type": "object", "properties": {}}

    def test_schema_fallback_invalid_type(self):
        """schema 非 dict（如字符串）时回退为空 object schema。"""
        adapter = _make_adapter(
            remote_tool={"name": "read", "description": "d", "schema": "not-a-schema"}
        )
        assert adapter.schema == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# description 来源标识
# ---------------------------------------------------------------------------


class TestDescription:
    """测试 description 的 [MCP: server] 来源标识。"""

    def test_description_has_source_marker(self):
        """description 透传远端描述并带来源标识。"""
        adapter = _make_adapter()
        assert adapter.description == "[MCP: fs] 读取文件内容"

    def test_description_fallback_to_remote_name(self):
        """远端无描述时回退为工具名。"""
        adapter = _make_adapter(remote_tool={"name": "read", "description": "", "schema": None})
        assert adapter.description == "[MCP: fs] read"


# ---------------------------------------------------------------------------
# execute 成功路径
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    """测试 execute 成功时返回 ToolResult.ok 且 data 正确。"""

    @pytest.mark.asyncio
    async def test_structured_content(self):
        """远端返回 structured_content 时优先作为结构化数据。"""
        client = FakeMCPClient(
            result=SimpleNamespace(structured_content={"result": 42}, content=[], is_error=False)
        )
        adapter = _make_adapter(client=client)
        result = await adapter.execute({"path": "/tmp/a.txt"})
        assert result.success is True
        assert result.data == {"result": 42}
        assert result.error is None
        assert result.tool_name == "mcp__fs__read"
        # 调用转发到远端工具名（不带 mcp__ 前缀）与原始参数
        assert client.calls == [("read", {"path": "/tmp/a.txt"})]

    @pytest.mark.asyncio
    async def test_text_content(self):
        """无 structured_content 时拼接全部文本块。"""
        client = FakeMCPClient(
            result=SimpleNamespace(
                structured_content=None,
                content=[SimpleNamespace(text="hello"), SimpleNamespace(text="world")],
                is_error=False,
            )
        )
        adapter = _make_adapter(client=client)
        result = await adapter.execute({})
        assert result.success is True
        assert result.data == "hello\nworld"

    @pytest.mark.asyncio
    async def test_mixed_content(self):
        """文本块 + 非文本块（如图片）时以 {text, content} 聚合。"""
        client = FakeMCPClient(
            result=SimpleNamespace(
                structured_content=None,
                content=[SimpleNamespace(text="desc"), {"type": "image", "data": "..."}],
                is_error=False,
            )
        )
        adapter = _make_adapter(client=client)
        result = await adapter.execute({})
        assert result.success is True
        assert result.data["text"] == "desc"
        assert len(result.data["content"]) == 1

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """空结果返回空字符串且视为成功。"""
        client = FakeMCPClient(
            result=SimpleNamespace(structured_content=None, content=[], is_error=False)
        )
        adapter = _make_adapter(client=client)
        result = await adapter.execute({})
        assert result.success is True
        assert result.data == ""


# ---------------------------------------------------------------------------
# execute 失败路径
# ---------------------------------------------------------------------------


class TestExecuteFailure:
    """测试 execute 失败时转为 ToolResult.fail 且不抛出未捕获异常。"""

    @pytest.mark.asyncio
    async def test_client_exception(self):
        """call_tool 抛异常 → ToolResult.fail，错误信息含 server/tool 定位。"""
        client = FakeMCPClient(error=RuntimeError("connection reset"))
        adapter = _make_adapter(client=client)
        result = await adapter.execute({})
        assert result.success is False
        assert result.error is not None
        assert "MCP 工具调用失败" in result.error
        assert "fs" in result.error
        assert "read" in result.error

    @pytest.mark.asyncio
    async def test_is_error_flag(self):
        """远端返回 isError=True → ToolResult.fail 并附远端错误文本。"""
        client = FakeMCPClient(
            result=SimpleNamespace(
                structured_content=None,
                content=[SimpleNamespace(text="远端错误: permission denied")],
                isError=True,
            )
        )
        adapter = _make_adapter(client=client)
        result = await adapter.execute({})
        assert result.success is False
        assert "远端 MCP 工具返回错误" in result.error
        assert "permission denied" in result.error

    @pytest.mark.asyncio
    async def test_is_error_snake_case(self):
        """兼容 snake_case 的 is_error 属性。"""
        client = FakeMCPClient(
            result=SimpleNamespace(structured_content=None, content=[], is_error=True)
        )
        adapter = _make_adapter(client=client)
        result = await adapter.execute({})
        assert result.success is False


# ---------------------------------------------------------------------------
# OpenAI schema 导出
# ---------------------------------------------------------------------------


class TestOpenAISchema:
    """测试 to_openai_schema() 输出格式。"""

    def test_to_openai_schema(self):
        """to_openai_schema 符合 OpenAI function calling 格式。"""
        adapter = _make_adapter()
        schema = adapter.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "mcp__fs__read"
        assert schema["function"]["description"] == "[MCP: fs] 读取文件内容"
        assert schema["function"]["parameters"]["type"] == "object"
