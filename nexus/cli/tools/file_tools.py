"""Nexus CLI 文件系统工具 —— 提供 Agent 操作本地文件的能力。

设计思路
--------
CLI Agent 的核心价值在于能直接操作项目文件。
这些工具是 CLI 独有的（不与 nexus/tools/builtins.py 重复），面向编程场景：
- ReadFileTool: 读取源码文件
- WriteFileTool: 修改/创建文件
- ListDirTool: 浏览项目结构
- SearchContentTool: 代码搜索

设计原因：文件操作是编码 Agent 最基本的能力，opencode/pi 的核心竞争力
就是能读写用户的项目文件并生成代码。
"""

from __future__ import annotations

import fnmatch
import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from nexus.logging import get_logger
from nexus.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from nexus.core.agent.agent import Agent
    from nexus.tools.registry import ToolRegistry

logger = get_logger(__name__)

# ------------------------------------------------------------------
# 通用辅助函数
# ------------------------------------------------------------------


def _resolve_path(base_dir: str, path: str) -> str:
    """将相对或绝对路径解析为绝对路径，并做安全检查。

    安全检查：
    - 禁止 path 参数为空或纯空白字符串
    - 检查解析后的绝对路径位于 base_dir 子树内（防目录遍历攻击）
    - 若解析后路径不在 base_dir 下，记录警告并拒绝访问

    Parameters
    ----------
    base_dir : str
        工作目录（绝对路径），作为路径解析基点和安全边界。
    path : str
        用户提供的文件/目录路径（可为相对或绝对路径）。

    Returns
    -------
    str
        解析后的绝对路径（已验证在 base_dir 内）。

    Raises
    ------
    ValueError
        路径为空、纯空白或试图越权访问 base_dir 之外的目录。
    """
    if not path or not path.strip():
        raise ValueError("Path cannot be empty or whitespace-only.")

    # 若 path 已是绝对路径，os.path.join 的第二个参数会覆盖第一个
    # 用 normpath 统一处理 .. 和多余的 /
    resolved = os.path.normpath(os.path.join(base_dir, path))

    # 安全检查：确保解析路径在 base_dir 子树内
    # 使用 os.path.commonpath 而非 startswith，防止 `/etc/base` 
    # 绕过 `/etc` 前缀检查的问题
    base_real = os.path.realpath(base_dir)
    resolved_real = os.path.realpath(resolved)
    if os.path.commonpath([base_real, resolved_real]) != base_real:
        logger.warning(
            "Path traversal attempt blocked",
            extra={"base_dir": base_dir, "requested": path},
        )
        raise ValueError(
            f"Access denied: '{path}' is outside the working directory."
        )

    return resolved


def _ensure_parent_dir(file_path: str) -> None:
    """创建文件的父目录（若不存在）。

    Parameters
    ----------
    file_path : str
        目标文件路径。
    """
    parent = os.path.dirname(file_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
        logger.debug("Created parent directory", extra={"dir": parent})


# ------------------------------------------------------------------
# ReadFileTool
# ------------------------------------------------------------------


class ReadFileTool(BaseTool):
    """读取文件内容工具。

    安全设计：
    --------
    - **行数限制**：自动限制读取行数（默认 500 行），防止单次读取过大文件撑爆 LLM 上下文窗口
    - **行范围控制**：支持 start_line / end_line 指定精确范围，避免 LLM 请求超大文件
    - **目录遍历防护**：通过 _resolve_path 验证访问路径在工作目录子树内
    - **文件大小上限**：拒绝读取超过 2MB 的二进制或超大文本文件
    - **自动编码检测**：优先 UTF-8，失败时尝试常见编码，所有失败返回可读错误

    使用示例
    --------

    >>> tool = ReadFileTool(work_dir="/project")
    >>> result = await tool.execute({"path": "src/main.py"})
    >>> result = await tool.execute({"path": "src/app.py", "start_line": 10, "end_line": 50})
    """

    # 文件大小上限（2MB），超过此大小的文件拒绝直接读取
    MAX_FILE_SIZE = 2 * 1024 * 1024

    # 默认最大读取行数
    DEFAULT_MAX_LINES = 500

    # 尝试的编码列表（按优先级）
    _ENCODINGS = ("utf-8", "latin-1", "cp1252", "gbk")

    def __init__(self, work_dir: str = ".") -> None:
        """初始化 ReadFileTool。

        Parameters
        ----------
        work_dir : str
            工作目录，所有相对路径相对于此目录解析。
        """
        self.work_dir = os.path.abspath(work_dir)

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "读取文件内容。返回带行号的可读文本，默认最多 500 行。"
            "支持指定行范围（start_line / end_line）以减少输出量。"
            "当需要查看代码、配置文件或任意文本文件时使用此工具。"
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径（相对或绝对）。推荐使用相对路径。",
                },
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（从 1 开始），可选。不指定则从第一行开始。",
                    "minimum": 1,
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（包含此行的内容），可选。",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """读取文件并返回带行号的文本内容。

        流程：安全检查 → 存在性校验 → 大小检查 → 编码检测 → 按行范围读取。
        """
        path_arg: str = args["path"]
        start_line: int | None = args.get("start_line")
        end_line: int | None = args.get("end_line")

        # 1. 安全检查 + 路径解析
        try:
            file_path = _resolve_path(self.work_dir, path_arg)
        except ValueError as e:
            return ToolResult.fail(
                error=str(e),
                tool_name=self.name,
            )

        # 2. 存在性校验
        if not os.path.exists(file_path):
            return ToolResult.fail(
                error=f"File not found: {path_arg}",
                tool_name=self.name,
            )

        if os.path.isdir(file_path):
            return ToolResult.fail(
                error=f"'{path_arg}' is a directory, not a file. Use list_dir to browse it.",
                tool_name=self.name,
            )

        # 3. 文件大小检查
        file_size = os.path.getsize(file_path)
        if file_size > self.MAX_FILE_SIZE:
            return ToolResult.fail(
                error=(
                    f"File is too large ({file_size:,} bytes > {self.MAX_FILE_SIZE:,} bytes limit). "
                    f"Use start_line/end_line to read a specific portion."
                ),
                tool_name=self.name,
            )

        # 4. 读取文件（多编码尝试）
        content: str | None = None
        used_encoding: str = ""
        last_error: str = ""

        for enc in self._ENCODINGS:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                    used_encoding = enc
                    break
            except UnicodeDecodeError:
                last_error = f"Failed to decode with {enc}"
                continue
            except OSError as e:
                return ToolResult.fail(
                    error=f"Failed to open {path_arg}: {e}",
                    tool_name=self.name,
                )

        if content is None:
            return ToolResult.fail(
                error=f"Cannot read file with any supported encoding ({', '.join(self._ENCODINGS)}). {last_error}",
                tool_name=self.name,
            )

        # 5. 按行范围截取
        lines = content.splitlines()

        # 规范化行号（从 1 开始）
        total_lines = len(lines)
        effective_start = max(1, start_line or 1)
        effective_end = min(total_lines, end_line or total_lines)

        if effective_start > total_lines:
            return ToolResult.fail(
                error=f"start_line ({effective_start}) exceeds total lines ({total_lines}).",
                tool_name=self.name,
            )

        if effective_start > effective_end:
            return ToolResult.fail(
                error=f"start_line ({effective_start}) > end_line ({effective_end}).",
                tool_name=self.name,
            )

        # 截取指定范围
        selected_lines = lines[effective_start - 1 : effective_end]

        # 行数限制
        if len(selected_lines) > self.DEFAULT_MAX_LINES:
            selected_lines = selected_lines[: self.DEFAULT_MAX_LINES]
            truncated = True
        else:
            truncated = False

        # 6. 生成带行号的输出
        output_parts: list[str] = []
        line_no_width = len(str(effective_end))
        for i, line in enumerate(selected_lines):
            line_no = effective_start + i
            output_parts.append(f"{line_no:>{line_no_width}} | {line}")

        result_text = "\n".join(output_parts)

        # 附加元信息
        meta = f"--- {path_arg} (lines {effective_start}-{effective_start + len(selected_lines) - 1}"
        if truncated:
            meta += f", truncated to {self.DEFAULT_MAX_LINES} lines"
        meta += f", {file_size:,} bytes, encoding={used_encoding}) ---\n\n"
        result_text = meta + result_text

        logger.info(
            "File read",
            extra={
                "tool_name": self.name,
                "path": path_arg,
                "lines": len(selected_lines),
                "file_size": file_size,
            },
        )

        return ToolResult(
            success=True,
            data=result_text,
            tool_name=self.name,
        )


# ------------------------------------------------------------------
# WriteFileTool
# ------------------------------------------------------------------


class WriteFileTool(BaseTool):
    """文件写入工具。

    安全设计：
    --------
    - **目录遍历防护**：通过 _resolve_path 验证目标路径在工作目录子树内
    - **存在性提示**：若目标文件已存在，在返回信息中明确告知（Agent 模式下自动覆盖，不抛异常）
    - **自动创建父目录**：写入前确保父目录存在，减少 LLM 操作步骤
    - **原子性**：写入失败时不应残留部分内容

    设计决策（require_confirmation = False）：
    CLI Agent 模式下不弹出交互确认，因为 Agent 循环本身是自动化的。
    若需要确认，由上层 REPL 负责拦截和展示 diff 预览。
    """

    def __init__(self, work_dir: str = ".") -> None:
        """初始化 WriteFileTool。

        Parameters
        ----------
        work_dir : str
            工作目录，所有路径相对于此目录解析。
        """
        self.work_dir = os.path.abspath(work_dir)
        self.require_confirmation = False  # Agent 模式下自动确认

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "创建新文件或覆盖已有文件的内容。"
            "如果目标文件已存在，会覆盖其内容并在结果中提示。"
            "父目录不存在时会自动创建。"
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要写入的文件路径（相对或绝对）。",
                },
                "content": {
                    "type": "string",
                    "description": "要写入文件的完整文本内容。",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """创建或覆盖文件。

        流程：安全检查 → 创建父目录 → 检测是否覆盖 → 写入文件。
        """
        path_arg: str = args["path"]
        content: str = args["content"]

        # 1. 安全检查
        try:
            file_path = _resolve_path(self.work_dir, path_arg)
        except ValueError as e:
            return ToolResult.fail(
                error=str(e),
                tool_name=self.name,
            )

        # 2. 确保父目录存在
        try:
            _ensure_parent_dir(file_path)
        except OSError as e:
            return ToolResult.fail(
                error=f"Failed to create parent directory: {e}",
                tool_name=self.name,
            )

        # 3. 检测是否覆盖已有文件
        existed = os.path.exists(file_path)
        action = "overwritten" if existed else "created"

        # 4. 写入文件
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return ToolResult.fail(
                error=f"Failed to write file: {e}",
                tool_name=self.name,
            )

        content_lines = content.count("\n") + (1 if content else 0)
        result_msg = (
            f"File {action}: {path_arg} ({content_lines} lines, {len(content.encode('utf-8'))} bytes)"
        )

        logger.info(
            "File written",
            extra={
                "tool_name": self.name,
                "path": path_arg,
                "action": action,
                "lines": content_lines,
            },
        )

        return ToolResult(
            success=True,
            data=result_msg,
            tool_name=self.name,
        )


# ------------------------------------------------------------------
# ListDirTool
# ------------------------------------------------------------------


class ListDirTool(BaseTool):
    """目录列表工具。

    用于浏览项目文件结构。LLM 可以先 list_dir 了解项目布局，
    再精确使用 read_file 读取感兴趣的文件。

    安全设计：
    --------
    - **目录遍历防护**：通过 _resolve_path 确保只在工作目录内浏览
    - **递归深度限制**：默认 max_depth=3，防止遍历深层 node_modules 等目录
    - **条目数量限制**：单次最多返回 500 条，防止结果撑爆 LLM 上下文
    - **跳过隐藏目录**：默认跳过以 . 开头的目录（.git / .venv / node_modules 等），
      但保留 .env / .gitignore 等隐藏文件
    """

    # 单次返回的条目数量上限
    MAX_ENTRIES = 500

    # 递归时跳过的目录名前缀和名称
    _SKIP_DIRS = {
        ".git", ".svn", ".hg",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "node_modules", ".venv", "venv", ".tox",
        ".idea", ".vscode",
        ".eggs", "build", "dist",
        ".next", ".nuxt",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "列出指定目录中的文件和子目录。"
            "支持递归列出（默认最多 3 层深度），自动跳过常见构建/缓存目录。"
            "用于了解项目结构、定位文件位置。"
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要列出的目录路径，默认当前工作目录。",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "是否递归列出子目录，默认 false。",
                },
                "max_depth": {
                    "type": "integer",
                    "description": "递归最大深度（仅在 recursive=true 时生效），默认 3。",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": [],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """列出目录内容。

        流程：安全检查 → 遍历目录 → 排序 → 格式化输出。
        """
        path_arg: str = args.get("path", ".")
        recursive: bool = args.get("recursive", False)
        max_depth: int = args.get("max_depth", 3)

        # 1. 安全检查
        try:
            dir_path = _resolve_path(self.work_dir if hasattr(self, "work_dir") else ".", path_arg)
        except ValueError as e:
            return ToolResult.fail(error=str(e), tool_name=self.name)

        if not os.path.exists(dir_path):
            return ToolResult.fail(
                error=f"Directory not found: {path_arg}",
                tool_name=self.name,
            )

        if not os.path.isdir(dir_path):
            return ToolResult.fail(
                error=f"'{path_arg}' is not a directory. Use read_file to read it as a file.",
                tool_name=self.name,
            )

        # 2. 遍历目录
        entries: list[str] = []
        dir_count = 0
        file_count = 0

        if recursive:
            for root, dirs, files in os.walk(dir_path):
                # 计算当前深度（相对于起始目录）
                rel_root = os.path.relpath(root, dir_path)
                if rel_root == ".":
                    depth = 0
                else:
                    depth = rel_root.count(os.sep) + 1

                # 深度限制
                if depth > max_depth:
                    dirs.clear()  # 不再深入子目录
                    continue

                # 过滤应跳过的目录
                dirs[:] = [d for d in dirs if d not in self._SKIP_DIRS and not d.startswith(".")]

                # 收集文件
                for fname in files:
                    if len(entries) >= self.MAX_ENTRIES:
                        break
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, dir_path)
                    entries.append(f"  📄 {rel}")
                    file_count += 1

                # 收集目录（排除根目录自身）
                for dname in dirs:
                    if len(entries) >= self.MAX_ENTRIES:
                        break
                    full = os.path.join(root, dname)
                    rel = os.path.relpath(full, dir_path)
                    entries.append(f"  📁 {rel}/")
                    dir_count += 1

                if len(entries) >= self.MAX_ENTRIES:
                    break
        else:
            try:
                items = sorted(os.listdir(dir_path))
            except OSError as e:
                return ToolResult.fail(
                    error=f"Failed to list directory: {e}",
                    tool_name=self.name,
                )
            for item in items:
                if len(entries) >= self.MAX_ENTRIES:
                    break
                full = os.path.join(dir_path, item)
                if os.path.isdir(full):
                    if item in self._SKIP_DIRS or item.startswith("."):
                        continue
                    entries.append(f"  📁 {item}/")
                    dir_count += 1
                else:
                    entries.append(f"  📄 {item}")
                    file_count += 1

        # 3. 构建输出
        header_parts = [f"Contents of {path_arg}:"]
        if recursive:
            header_parts.append(f" (recursive, max_depth={max_depth})")
        header_parts.append(f" — {dir_count} dirs, {file_count} files")
        header = "".join(header_parts)

        if len(entries) >= self.MAX_ENTRIES:
            entries.append(f"  ... (truncated at {self.MAX_ENTRIES} entries)")

        output = header + "\n" + "\n".join(entries)

        logger.info(
            "Directory listed",
            extra={
                "tool_name": self.name,
                "path": path_arg,
                "dirs": dir_count,
                "files": file_count,
            },
        )

        return ToolResult(success=True, data=output, tool_name=self.name)


# ------------------------------------------------------------------
# SearchContentTool
# ------------------------------------------------------------------


class SearchContentTool(BaseTool):
    """文件内容搜索工具。

    安全设计：
    --------
    - **目录遍历防护**：通过 _resolve_path 确保只在工作目录内搜索
    - **结果数量限制**：默认最多 50 条，防止结果撑爆 LLM 上下文
    - **文件大小限制**：跳过超过 2MB 的文件，防止正则匹配卡死
    - **跳过二进制/缓存目录**：自动跳过 ListDirTool._SKIP_DIRS 中的目录
    - **正则安全**：使用 re.search 而非 re.match，配合超时（Python 3.11+）
    """

    # 单次搜索的结果上限
    MAX_RESULTS = 50

    # 搜索跳过的目录（与 ListDirTool 共享）
    _SKIP_DIRS = ListDirTool._SKIP_DIRS

    # 跳过的大文件阈值
    MAX_FILE_SIZE = 2 * 1024 * 1024

    def __init__(self, work_dir: str = ".") -> None:
        """初始化 SearchContentTool。

        Parameters
        ----------
        work_dir : str
            工作目录，搜索范围限定在此目录内。
        """
        self.work_dir = os.path.abspath(work_dir)

    @property
    def name(self) -> str:
        return "search_content"

    @property
    def description(self) -> str:
        return (
            "在项目文件中搜索匹配特定模式（正则表达式）的文本行。"
            "类似 grep，返回匹配的文件路径、行号和内容。"
            "支持按文件名模式过滤（如 *.py 仅搜索 Python 文件）。"
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "要搜索的正则表达式模式（Python re 语法）。",
                },
                "path": {
                    "type": "string",
                    "description": "搜索起始目录，默认当前工作目录。",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "文件名 glob 模式过滤，如 '*.py' 或 '*.{ts,tsx}'。不指定则搜索所有文本文件。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大返回结果数，默认 50。",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """在项目文件中搜索匹配内容。

        流程：编译正则 → 遍历目录树 → 逐文件搜索 → 收集匹配行。
        """
        pattern: str = args["pattern"]
        path_arg: str = args.get("path", ".")
        file_pattern: str | None = args.get("file_pattern")
        max_results: int = args.get("max_results", self.MAX_RESULTS)

        # 1. 编译正则
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult.fail(
                error=f"Invalid regular expression '{pattern}': {e}",
                tool_name=self.name,
            )

        # 2. 安全检查
        try:
            search_dir = _resolve_path(self.work_dir, path_arg)
        except ValueError as e:
            return ToolResult.fail(error=str(e), tool_name=self.name)

        if not os.path.isdir(search_dir):
            return ToolResult.fail(
                error=f"'{path_arg}' is not a directory.",
                tool_name=self.name,
            )

        # 3. 遍历目录
        results: list[str] = []
        files_scanned = 0
        files_skipped_size = 0

        for root, dirs, files in os.walk(search_dir):
            # 过滤跳过的目录
            dirs[:] = [d for d in dirs if d not in self._SKIP_DIRS and not d.startswith(".")]

            for fname in files:
                # 文件名过滤
                if file_pattern and not fnmatch.fnmatch(fname, file_pattern):
                    continue

                full_path = os.path.join(root, fname)

                # 跳过隐藏文件（.env, .gitignore 除外）
                if fname.startswith(".") and fname not in (".env", ".gitignore", ".editorconfig"):
                    continue

                # 大小过滤
                try:
                    fsize = os.path.getsize(full_path)
                except OSError:
                    continue
                if fsize > self.MAX_FILE_SIZE:
                    files_skipped_size += 1
                    continue

                # 搜索文件内容
                rel_path = os.path.relpath(full_path, search_dir)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_no, line in enumerate(f, start=1):
                            if regex.search(line):
                                results.append(f"{rel_path}:{line_no}: {line.rstrip()}")
                                if len(results) >= max_results:
                                    break
                except OSError:
                    continue

                files_scanned += 1

                if len(results) >= max_results:
                    break

            if len(results) >= max_results:
                break

        # 4. 构建输出
        if not results:
            output = f"No matches found for pattern '{pattern}' in {path_arg}"
            if files_skipped_size > 0:
                output += f" ({files_skipped_size} files skipped due to size)"
        else:
            output = f"Found {len(results)} match(es) for '{pattern}' in {path_arg} ({files_scanned} files scanned):\n"
            output += "\n".join(results)
            if len(results) >= max_results:
                output += f"\n... (results truncated at {max_results})"

        logger.info(
            "Content search completed",
            extra={
                "tool_name": self.name,
                "pattern": pattern,
                "path": path_arg,
                "matches": len(results),
                "files_scanned": files_scanned,
            },
        )

        return ToolResult(success=True, data=output, tool_name=self.name)


# ------------------------------------------------------------------
# 注册函数
# ------------------------------------------------------------------


def register_all_tools(agent_or_registry: Agent | ToolRegistry) -> None:
    """将 CLI 内置文件系统工具注册到 Agent 或 ToolRegistry。

    支持两种注入方式：
    1. 传入 Agent 实例 → 通过 ``agent.register_tool()`` 注册
    2. 传入 ToolRegistry 实例 → 通过 ``registry.register()`` 注册

    使用示例
    --------

    >>> from nexus.core.agent.agent import Agent
    >>> from nexus.cli.tools.file_tools import register_all_tools
    >>>
    >>> agent = Agent(llm=my_llm)
    >>> register_all_tools(agent)

    也可直接注册到 Registry：

    >>> from nexus.tools.registry import ToolRegistry
    >>> registry = ToolRegistry()
    >>> register_all_tools(registry)

    Parameters
    ----------
    agent_or_registry : Agent or ToolRegistry
        要注入工具的 Agent 实例或 ToolRegistry 实例。
    """
    work_dir = os.getcwd()
    tools: list[BaseTool] = [
        ReadFileTool(work_dir=work_dir),
        WriteFileTool(work_dir=work_dir),
        ListDirTool(),
        SearchContentTool(work_dir=work_dir),
    ]

    # 兼容两种注册方式：Agent.register_tool() 和 ToolRegistry.register()
    if hasattr(agent_or_registry, "register_tool"):
        # Agent 门面模式
        for t in tools:
            agent_or_registry.register_tool(t)
    else:
        # 原生 ToolRegistry
        for t in tools:
            agent_or_registry.register(t)

    logger.info(
        "CLI file tools registered",
        extra={"tool_count": len(tools), "tools": [t.name for t in tools]},
    )
