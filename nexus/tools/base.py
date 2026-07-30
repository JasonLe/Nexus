"""Nexus Tool 系统基础抽象 —— BaseTool、ToolResult、ToolError。

设计思路
--------
使用类而非函数定义工具，原因如下：

1. **内部状态管理** —— 工具可以有连接池、缓存、限流器等内部状态。
   函数只能闭包捕获外部状态，不利于资源生命周期管理。
2. **继承与多态** —— 可定义工具族（如 ``CrudTool`` 基类），子类覆写部分行为。
   类提供自然的继承体系，函数装饰器链难以表达多级抽象。
3. **统一元数据获取** —— 通过 ``isinstance(obj, BaseTool)`` 即可安全获取
   name/description/schema，无需运行时反射检测。
4. **可测试性** —— 可 mock 工具实例，替换 execute 方法进行单元测试。
   函数式工具需要 mock 整个模块，粒度更粗。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nexus.logging import get_logger

logger = get_logger(__name__)


class ToolError(Exception):
    """工具系统中的基础异常类型。

    设计思路：为工具层提供统一的异常类型，便于上层（Runtime / Agent）统一捕获。
    子异常类型（ToolValidationError、ToolNotFoundError 等）继承自此基类，
    允许 caller 按需精细化捕获或统一兜底。
    """

    pass


class ToolValidationError(ToolError):
    """工具参数校验失败时抛出。"""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(f"[{tool_name}] {message}")
        self.tool_name = tool_name


class ToolNotFoundError(ToolError):
    """请求的工具名称未注册时抛出。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool not found: {tool_name}")
        self.tool_name = tool_name


@dataclass
class ToolResult:
    """工具执行结果。

    统一的数据类：无论工具执行成功还是失败，都返回 ToolResult。
    caller 通过 ``result.success`` 判断，无需捕获异常。
    这简化了上层（Agent 循环）的错误处理逻辑。

    Attributes
    ----------
    success : bool
        执行是否成功。
    data : Any
        成功时的返回值，可为任意类型（字符串、dict、PIL Image 等）。
    error : str | None
        失败时的错误描述文本，供 LLM 阅读后自行纠错。
    tool_name : str
        产生此结果的工具名称，便于日志追踪。
    timestamp : datetime
        结果产生时间（UTC）。
    duration_ms : float
        工具执行耗时（毫秒），用于性能监控。
    """

    success: bool
    data: Any = None
    error: str | None = None
    tool_name: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0

    @classmethod
    def ok(cls, data: Any, tool_name: str = "", duration_ms: float = 0.0) -> ToolResult:
        """快捷构造成功结果。"""
        return cls(
            success=True,
            data=data,
            tool_name=tool_name,
            duration_ms=duration_ms,
        )

    @classmethod
    def fail(cls, error: str, tool_name: str = "", duration_ms: float = 0.0) -> ToolResult:
        """快捷构造失败结果。"""
        return cls(
            success=False,
            error=error,
            tool_name=tool_name,
            duration_ms=duration_ms,
        )


class BaseTool(ABC):
    """工具抽象基类 —— 插件化工具框架的核心接口。

    设计思路
    --------
    所有工具通过统一接口接入 Agent：

    - 每个工具通过 ``name`` / ``description`` / ``schema`` 描述自身能力，
      供 LLM 发现和决策（Function Calling / Tool Use）。
    - Agent Runtime 通过 ``execute(args)`` 调用工具执行实际操作。
    - 返回 ``ToolResult``，不抛出未捕获异常 —— 即使是失败也通过
      ``ToolResult(success=False, error=...)`` 返回，让 LLM 自行解释和纠正。

    扩展约定
    --------
    - 子类必须定义 ``name``、``description``、``schema`` 属性。
    - 子类必须实现 ``execute(args)`` 异步方法。
    - ``execute`` 内部不应抛出未捕获异常，用 ``ToolResult.fail(...)`` 返回错误。
    - 若有初始化逻辑（如打开连接），放在 ``async def setup()``；若未定义则跳过。
    - 若有清理逻辑（如关闭连接），放在 ``async def teardown()``；若未定义则跳过。

    生命周期的设计决策
    ------------------
    BaseTool 不强制实现 setup/teardown，因为很多工具是无状态的（如计算器）。
    引入抽象方法会增加无意义样板代码。

    对于有状态工具（如数据库连接），可选实现：

    >>> async def setup(self) -> None:
    ...     self.pool = await create_pool(...)
    ...
    >>> async def teardown(self) -> None:
    ...     await self.pool.close()

    ToolExecutor 在调用 execute 前后会检查 setup/teardown 是否存在。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一名称，LLM 通过此名称识别和调用工具。

        命名规范
        --------
        - 使用小写 + 下划线（snake_case），如 ``web_search``、``file_read``。
        - 名称在 Registry 内必须唯一，重复注册会触发 ValueError。
        - 避免与 Python 内置关键字冲突。
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具功能描述，供 LLM 理解何时使用此工具。

        写作指南
        --------
        - 描述工具做什么，而非如何实现。
        - 包含参数类型提示和使用时机说明，帮助 LLM 正确选择工具。
        - 示例：``"在指定目录中按模式搜索文件，返回匹配的文件路径列表"``。
        """
        ...

    @property
    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """工具参数 schema（JSON Schema 格式）。

        示例
        ----

        .. code-block:: python

            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回数量",
                        "default": 10,
                    },
                },
                "required": ["query"],
            }

        注意事项
        --------
        - ``type`` 字段映射：Python ``list`` / ``tuple`` → JSON Schema ``"array"``，
          Python ``dict`` → ``"object"``。
        - ``required`` 列表中只列必需参数，可选参数不应出现。
        """
        ...

    @abstractmethod
    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """执行工具操作。

        Parameters
        ----------
        args : dict[str, Any]
            工具参数字典，已通过 ToolExecutor 按 schema 校验。
            校验保证 required 字段存在、类型匹配，executor 已过滤掉未知字段。

        Returns
        -------
        ToolResult
            包含执行结果或错误信息。不应返回 None 或抛出未捕获异常。
        """
        ...

    def to_openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 兼容格式。

        返回结构符合 OpenAI Chat Completions API 的 ``tools`` 参数格式。
        用于构造 LLM 请求时批量导出工具列表。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    @property
    def timeout(self) -> float | None:
        """工具执行超时（秒）。

        返回 ``None`` 表示使用 ToolExecutor 的默认超时。
        工具子类可覆写此属性自定义超时（如长时间运行的网络请求可设为 120s）。
        """
        return None

    async def setup(self) -> None:
        """可选：工具初始化逻辑（如建立连接、预热缓存）。

        若工具需要异步初始化（打开数据库连接池、下载模型等），覆写此方法。
        ToolExecutor 在首次调用前会调用此方法。
        """
        pass

    async def teardown(self) -> None:
        """可选：工具清理逻辑（如关闭连接、释放资源）。

        若工具持有外部资源，覆写此方法以正确释放。
        ToolExecutor 在 shutdown 时会调用此方法。
        """
        pass
