"""ToolExecutor —— 工具执行器，负责工具的校验、执行和结果记录。

设计思路
--------
在 BaseTool 和 Runtime 之间引入一个执行层，统一调度模型：

    **校验 → 执行 → 包装 → 记录**

为什么要独立一个 Executor 层：

1. **统一参数校验** —— 根据工具的 JSON Schema 校验入参（required 字段、类型匹配），
   避免每个工具重复实现校验逻辑，也避免 LLM 产生的畸形参数导致工具内部崩溃。
2. **统一错误处理** —— 捕获校验异常（ToolValidationError）、查找异常
   （ToolNotFoundError）和执行异常，全部包装为 ToolResult，上报层无需区分异常类型。
3. **调用记录与可观测性** —— 每次调用自动记录日志（工具名、call_id、耗时、
   success/error），未来可回写到 AgentState 用于调试和审计。
4. **扩展点** —— 未来可在此层加入调用限流（rate limit）、超时控制（asyncio.timeout）、
   重试策略、参数预处理等，工具开发者无需感知。

调度模型
--------
每一次 ``executor.execute(tool_name, tool_call_id, arguments)`` 调用，
执行以下完整流程：

.. code-block:: text

    1. 从 Registry 查找工具                 → ToolNotFoundError
    2. 校验参数（required / type）          → ToolValidationError
    3. setup()（若存在）                    → ToolError
    4. 调用 tool.execute(args) + 计时       → 任意异常
    5. 包装 ToolResult + 记录日志
    6. teardown()（若存在）                 → 记录警告（不阻塞结果）

其中步骤 1-3 的异常被捕获并转换为 ``ToolResult(success=False, error=...)``，
步骤 4 的工具自身异常同样被捕获转换。步骤 5 永远返回 ToolResult。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from nexus.tools.base import (
    BaseTool,
    ToolError,
    ToolNotFoundError,
    ToolResult,
    ToolValidationError,
)
from nexus.logging import get_logger

if TYPE_CHECKING:
    from nexus.tools.registry import ToolRegistry

logger = get_logger(__name__)

# JSON Schema type 到 Python type 的映射，用于基本参数校验。
_SCHEMA_TYPE_MAP: dict[str, type | tuple] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


class ToolExecutor:
    """工具执行器 —— 校验、执行、记录的统一调度层。

    生命周期
    --------
    - 构造时注入 ``ToolRegistry``，后续执行通过 Registry 查找工具。
    - 无自身状态，可安全复用，可在 Agent 循环中作为单例使用。

    Parameters
    ----------
    registry : ToolRegistry
        已注册工具的注册中心。

    使用示例
    --------

    >>> from nexus.tools.registry import ToolRegistry
    >>> from nexus.tools.executor import ToolExecutor
    >>>
    >>> registry = ToolRegistry()
    >>> registry.register(my_tool)
    >>> executor = ToolExecutor(registry)
    >>> result = await executor.execute(
    ...     tool_name="my_tool",
    ...     tool_call_id="call_abc123",
    ...     arguments={"param": "value"},
    ... )
    >>> if result.success:
    ...     print(result.data)
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry: ToolRegistry = registry

    def _validate_args(self, tool: BaseTool, args: dict[str, Any]) -> None:
        """按工具 schema 校验参数。

        校验逻辑（MVP 阶段，不引入 jsonschema 库）：

        1. 检查 ``required`` 列表中的字段是否存在。
        2. 检查每个已提供字段的类型是否与 schema 声明的 type 一致。
        3. 对 ``properties`` 中定义的每个字段单独校验，确保只校验已知字段。

        未来可替换为完整 ``jsonschema`` 库校验，接口不变。

        Parameters
        ----------
        tool : BaseTool
            待校验的工具实例（提供 schema）。
        args : dict[str, Any]
            待校验的参数。

        Raises
        ------
        ToolValidationError
            参数缺少 required 字段或类型不匹配。
        """
        schema = tool.schema
        properties: dict[str, dict[str, Any]] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])

        # 1. 检查 required 字段是否存在
        for field_name in required:
            if field_name not in args:
                raise ToolValidationError(
                    tool_name=tool.name,
                    message=(
                        f"Missing required parameter: '{field_name}'."
                    ),
                )

        # 2. 检查每个已提供字段的类型
        for field_name, value in args.items():
            if field_name not in properties:
                # 未知字段：MVP 阶段静默忽略（LLM 可能多传无关字段）
                # 未来可改为 strict 模式抛出 ToolValidationError
                continue

            prop_def = properties[field_name]
            expected_type_str: str | None = prop_def.get("type")

            if expected_type_str is None:
                # 字段无 type 声明，跳过类型检查
                continue

            expected_py_type = _SCHEMA_TYPE_MAP.get(expected_type_str)
            if expected_py_type is None:
                # 未知的 JSON Schema type（如 "array" 的 items 等），跳过
                continue

            # None 值的特殊处理：若值为 None 且不是 required，
            # 可能是故意不传，跳过类型检查
            if value is None:
                if field_name in required:
                    raise ToolValidationError(
                        tool_name=tool.name,
                        message=(
                            f"Parameter '{field_name}' cannot be null "
                            f"(required field)."
                        ),
                    )
                continue

            # 类型匹配检查
            if not isinstance(value, expected_py_type):
                raise ToolValidationError(
                    tool_name=tool.name,
                    message=(
                        f"Type mismatch for '{field_name}': "
                        f"expected {expected_type_str}, "
                        f"got {type(value).__name__}."
                    ),
                )

    async def execute(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        """执行工具调用（完整流程）。

        流程：查工具 → 校验参数 → 执行 → 包装 → 记录。

        Parameters
        ----------
        tool_name : str
            要调用的工具名称。
        tool_call_id : str
            工具调用唯一 ID（来自 LLM 响应的 tool_call.id）。
        arguments : dict[str, Any]
            工具参数。

        Returns
        -------
        ToolResult
            永远返回 ToolResult（success=True 或 False），不抛出异常。
        """
        t_start = time.perf_counter()

        # 1. 查找工具
        tool: BaseTool | None = self.registry.get(tool_name)
        if tool is None:
            elapsed = (time.perf_counter() - t_start) * 1000
            logger.warning(
                "Tool not found in registry",
                extra={"tool_name": tool_name, "tool_call_id": tool_call_id},
            )
            return ToolResult.fail(
                error=f"Tool '{tool_name}' is not registered.",
                tool_name=tool_name,
                duration_ms=elapsed,
            )

        # 2. 校验参数
        try:
            self._validate_args(tool, arguments)
        except ToolValidationError as e:
            elapsed = (time.perf_counter() - t_start) * 1000
            logger.warning(
                "Tool validation failed",
                extra={
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                },
            )
            return ToolResult.fail(
                error=str(e),
                tool_name=tool_name,
                duration_ms=elapsed,
            )

        # 3. setup()（若存在）
        try:
            await tool.setup()
        except Exception as e:
            elapsed = (time.perf_counter() - t_start) * 1000
            logger.error(
                "Tool setup failed",
                extra={"tool_name": tool_name, "tool_call_id": tool_call_id},
                exc_info=True,
            )
            return ToolResult.fail(
                error=f"Tool setup error: {e}",
                tool_name=tool_name,
                duration_ms=elapsed,
            )

        # 4. 执行工具
        try:
            result: ToolResult = await tool.execute(arguments)
        except Exception as e:
            elapsed = (time.perf_counter() - t_start) * 1000
            logger.error(
                "Tool execution raised exception",
                extra={"tool_name": tool_name, "tool_call_id": tool_call_id},
                exc_info=True,
            )
            return ToolResult.fail(
                error=f"Tool execution error: {e}",
                tool_name=tool_name,
                duration_ms=elapsed,
            )

        # 5. 包装结果
        elapsed = (time.perf_counter() - t_start) * 1000
        result.tool_name = tool_name
        result.duration_ms = elapsed

        # 6. teardown()（若存在）
        try:
            await tool.teardown()
        except Exception:
            # teardown 失败不应影响返回结果，仅以 warning 记录
            logger.warning(
                "Tool teardown raised exception",
                extra={"tool_name": tool_name, "tool_call_id": tool_call_id},
                exc_info=True,
            )

        # 记录日志
        if result.success:
            logger.info(
                "Tool executed successfully",
                extra={
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                },
            )
        else:
            logger.warning(
                "Tool executed with failure",
                extra={
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                },
            )

        return result
