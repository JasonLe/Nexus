"""@tool 装饰器 —— 将类标记为 Nexus Tool 并自动注入元数据。

设计思路
--------
装饰器模式封装工具的元数据定义，减少样板代码。

与直接覆写 ``name`` / ``description`` / ``schema`` 属性等效，但：

- 元数据集中在装饰器参数中，视觉上更清晰。
- 避免属性拼写错误（IDE 补全装饰器参数）。
- 类型检查器可以正确推断类属性。

使用示例
--------

.. code-block:: python

    from nexus.tools.base import BaseTool, ToolResult
    from nexus.tools.decorators import tool

    @tool(
        name="calculator",
        description="执行数学计算，支持加减乘除和常用数学函数",
        schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，如 '2 + 3 * 4'",
                },
            },
            "required": ["expression"],
        },
    )
    class CalculatorTool(BaseTool):
        async def execute(self, args: dict[str, Any]) -> ToolResult:
            try:
                result = eval(args["expression"], {"__builtins__": {}})
                return ToolResult.ok(data=result)
            except Exception as e:
                return ToolResult.fail(error=str(e))

注意事项
--------
- 装饰器仅在类定义时注入属性，不影响类本身的继承关系。
- 被装饰的类必须继承自 ``BaseTool``（或提供了 ``execute`` 方法的子类）。
- 运行时类型检查：若装饰的参数与 BaseTool 抽象接口不匹配，会在类实例化或
  ``to_openai_schema()`` 调用时报错，而非静默失败。
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from nexus.tools.base import BaseTool

_T = TypeVar("_T", bound=type[BaseTool])


def tool(
    name: str,
    description: str,
    schema: dict[str, Any],
) -> Callable[[_T], _T]:
    """工具装饰器 —— 将类标记为 Nexus Tool 并自动注册元数据。

    通过在类定义时注入三个属性（name、description、schema），
    免去子类手动定义抽象属性的样板代码。

    Parameters
    ----------
    name : str
        工具唯一名称（snake_case），如 ``"web_search"``。
    description : str
        工具功能描述，供 LLM 理解何时调用。
    schema : dict[str, Any]
        工具参数 JSON Schema，定义入参结构和约束。

    Returns
    -------
    Callable[[type], type]
        装饰后的类，已注入 name / description / schema 属性。
    """

    def decorator(cls: _T) -> _T:
        # 注入 property getter，使 name/description/schema 可以像属性一样访问。
        # 使用闭包绑定装饰器参数，避免并发修改问题。
        cls.name = property(lambda self, _n=name: _n)  # type: ignore[assignment]
        cls.description = property(lambda self, _d=description: _d)  # type: ignore[assignment]
        cls.schema = property(lambda self, _s=schema: _s)  # type: ignore[assignment]

        # 关键：清除 ABC 记录的抽象方法标记。
        # ABC 在类创建时将未实现的 abstractmethod 记入 __abstractmethods__ frozenset，
        # 防止类被实例化。装饰器已注入具体实现，需从该集合中移除对应条目。
        abstract: frozenset[str] = getattr(cls, "__abstractmethods__", frozenset())
        cls.__abstractmethods__ = frozenset(  # type: ignore[attr-defined]
            a for a in abstract if a not in ("name", "description", "schema")
        )

        return cls

    return decorator
