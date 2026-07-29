"""内置示例工具 —— 提供开箱即用的基础工具，方便测试和示例。

设计思路：内置工具作为框架的"Hello World"示范，展示 BaseTool 的正确实现方式。
每个工具覆盖不同场景（计算、IO、搜索），作为第三方开发者的参考模板。
"""

from __future__ import annotations

import json
from typing import Any

from nexus.logging import get_logger
from nexus.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)


class CalculatorTool(BaseTool):
    """计算器工具 —— 安全地执行数学表达式求值。

    设计思路：使用 Python 内置 eval 计算简单的数学表达式。
    生产环境中应替换为沙箱化的计算引擎（如 numexpr 或自定义 parser），
    此处为了示例简洁性直接使用 eval。

    安全提示：本实现仅为示例。若用于生产，必须对 expression 进行严格的
    输入校验（白名单允许的操作符和函数），或使用 AST 解析方式安全求值。
    """

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return (
            "执行数学计算。支持基本四则运算（+、-、*、/）、幂运算（**）、"
            "括号和常用数学函数（abs、round、min、max）。"
            "输入一个数学表达式字符串，返回计算结果。"
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，如 '2 + 3 * 4' 或 'round(3.14159, 2)'",
                }
            },
            "required": ["expression"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """执行数学表达式求值。

        使用受限的命名空间来防止任意代码执行。
        """
        expression: str = args["expression"]
        logger.debug(
            "Calculator evaluating expression",
            extra={"tool_name": self.name, "expression": expression},
        )

        try:
            # 使用受限命名空间，仅暴露安全的数学函数
            allowed_names: dict[str, Any] = {
                "abs": abs,
                "round": round,
                "min": min,
                "max": max,
                "pow": pow,
                "int": int,
                "float": float,
            }
            result = eval(expression, {"__builtins__": {}}, allowed_names)
            return ToolResult(
                success=True,
                data=result,
                tool_name=self.name,
            )
        except Exception as e:
            logger.warning(
                "Calculator evaluation failed",
                extra={"tool_name": self.name, "expression": expression},
            )
            return ToolResult(
                success=False,
                error=f"计算错误：{e}",
                tool_name=self.name,
            )


class EchoTool(BaseTool):
    """Echo 工具 —— 原样返回输入内容。

    设计思路：最简单的工具实现，用于：
    1. 验证工具系统的注册、查找、执行流程是否正常
    2. 作为"Hello World"级别的参考模板
    3. 测试和调试时作为 mock 工具
    """

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "原样返回输入的消息内容。用于测试工具调用流程是否正常工作。"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要回显的消息内容",
                }
            },
            "required": ["message"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """原样返回消息内容。"""
        message: str = args["message"]
        logger.debug(
            "Echo tool called",
            extra={"tool_name": self.name, "message": message},
        )
        return ToolResult(
            success=True,
            data=message,
            tool_name=self.name,
        )


class CurrentTimeTool(BaseTool):
    """获取当前日期时间工具。

    设计思路：展示一个轻量级工具，不依赖外部 API，返回结构化数据。
    """

    from datetime import datetime, timezone

    @property
    def name(self) -> str:
        return "get_current_time"

    @property
    def description(self) -> str:
        return (
            "获取当前的日期和时间（UTC）。"
            "不需要任何参数，返回 ISO 8601 格式的时间字符串。"
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """返回当前 UTC 时间。"""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        result_data = {
            "iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
        }
        logger.debug(
            "Current time tool called",
            extra={"tool_name": self.name},
        )
        return ToolResult(
            success=True,
            data=result_data,
            tool_name=self.name,
        )
