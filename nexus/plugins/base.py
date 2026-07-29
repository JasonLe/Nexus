"""Plugin 抽象层 - 定义可扩展 Agent 能力的插件接口。

设计目标
--------
Plugin 模式是框架可扩展性的核心支柱，让第三方可以在不修改 Core 的前提下
扩展框架能力。每个 Plugin 是一个自包含的功能包，可以对 Agent 进行多维度扩展。

与 VSCode Extension 的类比
~~~~~~~~~~~~~~~~~~~~~~~~~~
VSCode Extension 通过 ``package.json`` 声明贡献点（commands、menus、views 等），
通过 ``activate()`` / ``deactivate()`` 管理生命周期。Nexus Plugin 借鉴了同样的设计：

- **VSCode Extension** 注册 commands → **Nexus Plugin** 通过 ``agent.tool_registry.register`` 注册工具
- **VSCode Extension** 订阅 ``onDidChangeTextDocument`` → **Nexus Plugin** 通过 ``agent.events.subscribe`` 订阅事件
- **VSCode Extension** 注入 TreeView → **Nexus Plugin** 未来可对接 Web UI 面板
- **VSCode Extension** 的 ``activationEvents`` → **Nexus Plugin** 的 ``install`` 即为激活入口

Plugin 的能力扩展点
~~~~~~~~~~~~~~~~~~~
一个 Plugin 可以通过 ``install(agent)`` 中的 agent 引用实现以下扩展：

- **注册工具**：``agent.tool_registry.register(your_tool)`` — 为 Agent 添加新的可调用能力
- **订阅事件**：``agent.events.subscribe(event_type, handler)`— 在关键生命周期节点插入自定义逻辑
- **注入 Memory**：通过 agent 接口替换或增强记忆系统
- **扩展 Workflow**：注册新的 Workflow 步骤类型
- **UI 面板**：未来对接 Web UI，提供自定义界面

生命周期详解
~~~~~~~~~~~~
Plugin 的生命周期分为三个阶段，由 Agent 在适当时机调用：

1. **install(agent)** — 插件被安装到 Agent
   - 调用时机：Agent 初始化时加载已配置的插件，或运行时动态安装。
   - 在此方法中应完成：注册工具、订阅事件、初始化插件内部状态。
   - 此阶段不应启动后台任务或打开外部连接，仅做"声明式"注册。
   - agent 参数提供了插件的所有扩展入口。

2. **activate()** — 插件被激活
   - 调用时机：所有插件 install 完成后，Agent 准备就绪时。
   - 在此方法中应完成：启动后台任务、建立持久连接、开启监听器。
   - 此阶段表示插件正式开始工作。

3. **deactivate()** — 插件被停用
   - 调用时机：Agent 关闭或插件被卸载时。
   - 在此方法中应完成：释放资源、关闭连接、取消事件订阅、停止后台任务。
   - 实现应确保幂等——重复调用 deactivate 不会出错。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.core.agent import Agent

from nexus.logging import get_logger

logger = get_logger(__name__)


class Plugin(ABC):
    """插件抽象基类 - 类似 VSCode Extension 的插件机制。

    每个 Plugin 是一个自包含的功能包，可以扩展 Agent 的多种能力：
    工具注册、事件订阅、Memory 注入、Workflow 步骤、Web UI 面板等。

    Notes
    -----
    - ``name`` 必须唯一，用于 PluginRegistry 中的索引和冲突检测。
    - ``version`` 使用语义化版本（SemVer），用于依赖解析和兼容性检查。
    - 所有生命周期方法都是 async，因为安装/激活过程可能涉及 I/O。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """插件唯一名称，用于注册中心的索引和冲突检测。"""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """插件版本号，遵循语义化版本规范（SemVer）。"""
        ...

    @abstractmethod
    async def install(self, agent: "Agent") -> None:
        """安装插件到 Agent。

        在 Agent 初始化时或运行时动态安装时调用。此方法中应完成
        "声明式"注册操作，不应启动后台任务或打开外部连接。

        Parameters
        ----------
        agent : Agent
            目标 Agent 实例，提供以下扩展入口：

            - ``agent.tool_registry.register(tool)`` — 注册工具
            - ``agent.events.subscribe(event_type, handler)`` — 订阅事件
            - 其他 agent 接口（Memory、Workflow 等）
        """
        ...

    @abstractmethod
    async def activate(self) -> None:
        """激活插件 - 所有 install 完成后，正式开始工作。

        在此方法中启动后台任务、建立持久连接、开启监听器等。
        表示插件进入"工作中"状态。
        """
        ...

    @abstractmethod
    async def deactivate(self) -> None:
        """停用插件 - 释放资源、取消订阅、停止后台任务。

        实现应确保幂等性——重复调用 deactivate 不会出错。
        应在 Agent 关闭或插件卸载时调用。
        """
        ...
