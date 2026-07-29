"""Plugins 模块 - 可扩展 Agent 能力的插件系统。

提供插件抽象基类（Plugin）和插件注册中心（PluginRegistry），
让第三方可以在不修改 Core 的前提下扩展框架能力。
"""

from nexus.plugins.base import Plugin
from nexus.plugins.registry import PluginRegistry

__all__ = ["Plugin", "PluginRegistry"]
