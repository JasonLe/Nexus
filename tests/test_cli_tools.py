"""测试文件系统工具：ReadFileTool、WriteFileTool、ListDirTool、SearchContentTool、register_all_tools。

覆盖范围
--------
- ReadFileTool 读取文件成功 / 文件不存在 / 行范围读取 / 超行数截断
- WriteFileTool 写入文件成功 / 嵌套路径自动创建父目录
- ListDirTool 列出目录 / 递归列出
- SearchContentTool 搜索到内容 / 搜索无结果
- register_all_tools 注册到 ToolRegistry
"""

import pytest

from nexus.tools.file_tools import (
    ReadFileTool,
    WriteFileTool,
    ListDirTool,
    SearchContentTool,
    register_all_tools,
)
from nexus.tools.registry import ToolRegistry
from nexus.tools.base import ToolResult


# ---------------------------------------------------------------------------
# ReadFileTool 测试
# ---------------------------------------------------------------------------


class TestReadFileTool:
    """测试 ReadFileTool。"""

    @pytest.mark.asyncio
    async def test_read_file_success(self, tmp_path):
        """创建临时文件，读取应成功并返回带行号的内容。"""
        file_path = tmp_path / "hello.py"
        file_path.write_text("print('hello')\nprint('world')\n", encoding="utf-8")

        tool = ReadFileTool(work_dir=str(tmp_path))
        result = await tool.execute({"path": "hello.py"})

        assert result.success is True
        assert "import" not in result.data  # no import line
        assert "1 | print('hello')" in result.data
        assert "2 | print('world')" in result.data

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, tmp_path):
        """文件不存在时应返回 success=False。"""
        tool = ReadFileTool(work_dir=str(tmp_path))
        result = await tool.execute({"path": "nonexistent.py"})

        assert result.success is False
        assert result.error is not None
        assert "File not found" in result.error

    @pytest.mark.asyncio
    async def test_read_file_with_lines(self, tmp_path):
        """指定 start_line 和 end_line 范围读取应只返回范围内的行。"""
        content = "\n".join(f"line {i}" for i in range(1, 11))  # 10 lines
        file_path = tmp_path / "lines.txt"
        file_path.write_text(content, encoding="utf-8")

        tool = ReadFileTool(work_dir=str(tmp_path))
        result = await tool.execute({"path": "lines.txt", "start_line": 3, "end_line": 5})

        assert result.success is True
        assert "3 | line 3" in result.data
        assert "4 | line 4" in result.data
        assert "5 | line 5" in result.data
        # 不应包含范围外的行
        assert "2 |" not in result.data.split("\n\n", 1)[1] if "\n\n" in result.data else True
        assert "6 |" not in result.data.split("\n\n", 1)[1] if "\n\n" in result.data else True

    @pytest.mark.asyncio
    async def test_read_file_max_lines(self, tmp_path):
        """超大文件应仅返回前 500 行，并在元信息中标注截断。"""
        content = "\n".join(f"line {i}" for i in range(1, 601))  # 600 lines
        file_path = tmp_path / "large.txt"
        file_path.write_text(content, encoding="utf-8")

        tool = ReadFileTool(work_dir=str(tmp_path))
        result = await tool.execute({"path": "large.txt"})

        assert result.success is True
        # 应只返回 500 行
        line_count = result.data.count("\n") + 1
        assert line_count <= 501 + 3  # 500 data lines + metadata lines are a few
        assert "truncated to 500 lines" in result.data

    @pytest.mark.asyncio
    async def test_read_directory_fails(self, tmp_path):
        """读取目录路径时应返回失败。"""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        tool = ReadFileTool(work_dir=str(tmp_path))
        result = await tool.execute({"path": "subdir"})

        assert result.success is False
        assert "directory" in result.error.lower()


# ---------------------------------------------------------------------------
# WriteFileTool 测试
# ---------------------------------------------------------------------------


class TestWriteFileTool:
    """测试 WriteFileTool。"""

    @pytest.mark.asyncio
    async def test_write_file_success(self, tmp_path):
        """写入新文件应成功，文件内容正确。"""
        tool = WriteFileTool(work_dir=str(tmp_path))
        result = await tool.execute({"path": "output.txt", "content": "hello nexus"})

        assert result.success is True
        assert "created" in result.data

        written = (tmp_path / "output.txt").read_text(encoding="utf-8")
        assert written == "hello nexus"

    @pytest.mark.asyncio
    async def test_write_file_with_nested_path(self, tmp_path):
        """写入嵌套路径文件时应自动创建父目录。"""
        tool = WriteFileTool(work_dir=str(tmp_path))
        result = await tool.execute({
            "path": "deep/nested/dir/file.txt",
            "content": "deep content",
        })

        assert result.success is True
        file_path = tmp_path / "deep" / "nested" / "dir" / "file.txt"
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == "deep content"

    @pytest.mark.asyncio
    async def test_write_file_overwrite(self, tmp_path):
        """覆盖已有文件时应提示 overwritten。"""
        file_path = tmp_path / "existing.txt"
        file_path.write_text("old content", encoding="utf-8")

        tool = WriteFileTool(work_dir=str(tmp_path))
        result = await tool.execute({"path": "existing.txt", "content": "new content"})

        assert result.success is True
        assert "overwritten" in result.data
        assert file_path.read_text(encoding="utf-8") == "new content"


# ---------------------------------------------------------------------------
# ListDirTool 测试
# ---------------------------------------------------------------------------


class TestListDirTool:
    """测试 ListDirTool。"""

    @pytest.mark.asyncio
    async def test_list_dir(self, tmp_path):
        """列出目录内容应返回文件和子目录列表。"""
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.txt").write_text("", encoding="utf-8")
        subdir = tmp_path / "src"
        subdir.mkdir()

        tool = ListDirTool()
        # Patch work_dir to use tmp_path
        tool.work_dir = str(tmp_path)
        result = await tool.execute({"path": str(tmp_path)})

        assert result.success is True
        assert "a.py" in result.data
        assert "b.txt" in result.data
        assert "src" in result.data

    @pytest.mark.asyncio
    async def test_list_dir_recursive(self, tmp_path):
        """递归列出应包含子目录中的文件。"""
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("", encoding="utf-8")
        (tmp_path / "README.md").write_text("", encoding="utf-8")

        tool = ListDirTool()
        tool.work_dir = str(tmp_path)
        result = await tool.execute({"path": str(tmp_path), "recursive": True, "max_depth": 3})

        assert result.success is True
        assert "main.py" in result.data
        assert "README.md" in result.data


# ---------------------------------------------------------------------------
# SearchContentTool 测试
# ---------------------------------------------------------------------------


class TestSearchContentTool:
    """测试 SearchContentTool。"""

    @pytest.mark.asyncio
    async def test_search_content_found(self, tmp_path):
        """搜索存在的模式应返回匹配的结果。"""
        (tmp_path / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        tool = SearchContentTool(work_dir=str(tmp_path))
        result = await tool.execute({"pattern": r"def hello", "path": str(tmp_path)})

        assert result.success is True
        assert "app.py" in result.data
        assert "def hello" in result.data

    @pytest.mark.asyncio
    async def test_search_content_not_found(self, tmp_path):
        """搜索不存在的模式应返回无结果信息。"""
        (tmp_path / "app.py").write_text("print('hello')", encoding="utf-8")

        tool = SearchContentTool(work_dir=str(tmp_path))
        result = await tool.execute({"pattern": r"nonexistent_pattern_xyz", "path": str(tmp_path)})

        assert result.success is True
        assert "No matches found" in result.data

    @pytest.mark.asyncio
    async def test_search_with_file_pattern(self, tmp_path):
        """使用 file_pattern 过滤应仅搜索匹配的文件。"""
        (tmp_path / "app.py").write_text("TODO: fix this", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("TODO: update docs", encoding="utf-8")

        tool = SearchContentTool(work_dir=str(tmp_path))
        result = await tool.execute({
            "pattern": r"TODO",
            "path": str(tmp_path),
            "file_pattern": "*.py",
        })

        assert result.success is True
        assert "app.py" in result.data
        assert "notes.txt" not in result.data


# ---------------------------------------------------------------------------
# register_all_tools 测试
# ---------------------------------------------------------------------------


class TestRegisterAllTools:
    """测试 register_all_tools 注册函数。"""

    def test_register_to_tool_registry(self, tmp_path):
        """注册到 ToolRegistry 应包含 4 个工具。"""
        registry = ToolRegistry()
        # register_all_tools 使用 os.getcwd() 作为 work_dir
        # 我们将 work_dir 指向 tmp_path 以保持隔离
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            register_all_tools(registry)
        finally:
            os.chdir(original_cwd)

        tools = registry.list()
        tool_names = {t.name for t in tools}
        assert tool_names == {"read_file", "write_file", "list_dir", "search_content"}
        assert len(tools) == 4

    def test_register_all_tools_idempotent_raises(self, tmp_path):
        """重复注册到同一个 ToolRegistry 应抛出 ValueError。"""
        registry = ToolRegistry()
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            register_all_tools(registry)
            with pytest.raises(ValueError, match="already registered"):
                register_all_tools(registry)
        finally:
            os.chdir(original_cwd)
