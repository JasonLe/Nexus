"""ToolRegistry —— 工具注册中心，管理所有可用工具的注册与查询。

设计思路
--------
中心化注册 + 查询模式：

- Agent / ToolExecutor 通过 Registry 发现和获取工具实例。
- Plugin 系统协作时，``Plugin.install()`` 内部调用 ``registry.register()``
  将其携带的工具注入 Registry。
- 通过名称索引（dict）实现 O(1) 查找，满足工具调用热路径性能要求。

线程安全决策
------------
当前 MVP 阶段为单线程 async 事件循环，暂未引入锁机制。
原因：
1. 工具注册通常发生在启动阶段（Plugin 加载），极少与运行时并发。
2. 即使未来需要并发，上层（PluginManager）可通过事件循环调度避免竞争。
3. 避免过早优化 —— 若后续确实需要，在 register/list 中引入 ``asyncio.Lock``
   或使用 ``threading.RLock`` 即可，接口无需变更。

命名冲突策略
------------
- **重复注册同名工具 → 抛出 ValueError**。
  原因：静默覆盖可能导致 Plugin 加载顺序影响行为，调试困难。
- 若确实需要替换工具（如 Mock），调用 ``unregister()`` 后再 ``register()``。
"""

from __future__ import annotations

from typing import Any, Iterator


from nexus.tools.base import BaseTool


class ToolRegistry:
    """工具注册中心。

    维护 ``name → BaseTool`` 的映射，提供注册、查找、列表遍历和 OpenAI schema 导出。

    使用示例
    --------

    >>> from nexus.tools.registry import ToolRegistry
    >>> from nexus.tools.base import BaseTool
    >>>
    >>> registry = ToolRegistry()
    >>> registry.register(my_tool)
    >>> tool = registry.get("my_tool")
    >>> for tool in registry:
    ...     print(tool.name)
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具。

        将工具实例以名称索引存入 Registry。若名称已存在则
        抛出 ``ValueError``，防止意外覆盖。

        Parameters
        ----------
        tool : BaseTool
            待注册的工具实例。

        Raises
        ------
        ValueError
            同名工具已注册。
        """
        name = tool.name
        if name in self._tools:
            raise ValueError(
                f"Tool '{name}' is already registered. "
                f"Existing: {type(self._tools[name]).__name__}, "
                f"New: {type(tool).__name__}. "
                f"Use unregister('{name}') first if replacement is intended."
            )
        self._tools[name] = tool

    def unregister(self, name: str) -> BaseTool | None:
        """按名称移除工具。

        若工具存在，返回被移除的工具实例（便于调用方执行清理）；
        若不存在，返回 None。

        Parameters
        ----------
        name : str
            工具名称。

        Returns
        -------
        BaseTool or None
            被移除的工具实例，若名称不存在则返回 None。
        """
        return self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool | None:
        """按名称获取工具。

        Parameters
        ----------
        name : str
            工具名称。

        Returns
        -------
        BaseTool or None
            对应的工具实例，若未找到返回 None。
        """
        return self._tools.get(name)

    def list(self) -> list[BaseTool]:
        """获取所有已注册工具。

        返回列表的排列顺序是插入顺序（Python 3.7+ dict 保序）。

        Returns
        -------
        list[BaseTool]
            所有已注册工具的列表。
        """
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        """导出所有工具为 OpenAI function calling 兼容格式。

        遍历所有已注册工具，调用其 ``to_openai_schema()`` 方法生成
        ``tools`` 数组，可直接嵌入 LLM API 请求。

        Returns
        -------
        list[dict[str, Any]]
            OpenAI tools 参数格式的列表。
        """
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def __iter__(self) -> Iterator[BaseTool]:
        """迭代所有已注册工具（支持 ``for tool in registry`` 语法）。"""
        return iter(self._tools.values())

    def __len__(self) -> int:
        """返回已注册工具数量。"""
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """支持 ``"tool_name" in registry`` 语法。"""
        return name in self._tools
