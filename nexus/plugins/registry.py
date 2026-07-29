"""PluginRegistry - 插件注册中心。

设计定位
--------
PluginRegistry 是插件生命周期的集中管理者。它不负责插件的发现或加载
（发现/加载由 Agent 或外部配置驱动），只负责已加载插件的注册、索引和
生命周期调度。

职责边界
~~~~~~~~
- **负责**：注册/注销、按名称查找、列出所有插件、生命周期调度（activate/deactivate）。
- **不负责**：插件发现（扫描目录/入口点）、依赖解析、版本兼容性检查。
  这些功能属于 PluginManager 的职责，在当前 MVP 阶段暂不实现。

与 Agent 的关系
~~~~~~~~~~~~~~~
- ``Agent.install_plugin(plugin)`` 委托给 Registry 完成注册和激活。
- Agent 持有 Registry 实例，在关闭时遍历调用 ``deactivate()``。
- 插件通过 ``install(agent)`` 获得 Agent 引用，不应直接访问 Registry。

当前 MVP 状态
~~~~~~~~~~~~~
仅预留接口，完整实现（activate/deactivate 调度、插件间依赖解析、
严格的生命周期状态机）在后续阶段完善。
"""

from __future__ import annotations

from nexus.plugins.base import Plugin
from nexus.logging import get_logger

logger = get_logger(__name__)


class PluginRegistry:
    """插件注册中心 - 管理所有已安装插件的生命周期。

    Attributes
    ----------
    _plugins : dict[str, Plugin]
        以插件名称为键的内部存储。
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}

    async def register(self, plugin: Plugin) -> None:
        """注册插件（安装 + 激活）。

        依次调用 ``plugin.install(agent)`` 和 ``plugin.activate()``，
        然后将插件加入内部索引。install 阶段所需的 agent 引用由调用方传入。

        Parameters
        ----------
        plugin : Plugin
            待注册的插件实例。

        Raises
        ------
        ValueError
            如果同名插件已注册。
        """
        if plugin.name in self._plugins:
            logger.warning("Plugin %s already registered, skipping", plugin.name)
            return
        await plugin.activate()
        self._plugins[plugin.name] = plugin
        logger.info("Plugin %s registered", plugin.name)

    async def unregister(self, name: str) -> None:
        """注销插件（停用 + 移除）。

        先调用 ``plugin.deactivate()`` 释放资源，再从索引中移除。

        Parameters
        ----------
        name : str
            要注销的插件名称。
        """
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            logger.warning("Plugin %s not found for unregister", name)
            return
        await plugin.deactivate()
        logger.info("Plugin %s unregistered", name)

    def get(self, name: str) -> Plugin | None:
        """按名称获取插件。

        Parameters
        ----------
        name : str
            插件名称。

        Returns
        -------
        Plugin | None
            找到的插件实例，未找到时返回 None。
        """
        return self._plugins.get(name)

    def list(self) -> list[Plugin]:
        """列出所有已注册插件。

        Returns
        -------
        list[Plugin]
            所有已注册插件的列表。
        """
        return list(self._plugins.values())
