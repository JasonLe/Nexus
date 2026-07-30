"""Agent 门面类 —— Nexus 框架的顶层入口。

设计思路
--------
Agent 是用户交互的唯一入口（Facade 模式）。
它封装了 Runtime / ToolRegistry / PluginRegistry / EventBus，
提供简洁的配置+执行 API。

用户不需要直接操作 Runtime 或 Policy 细节，
通过 Agent 完成：配置 LLM → 注册工具 → 安装插件 → 订阅事件 → 执行任务。

设计决策
--------
- **Facade 模式**：Agent 对外暴露简单接口，内部委托给 Runtime 执行复杂调度。
- **快捷访问**：通过属性代理让用户可以直接用 agent.tool_registry 注册工具，
  无需了解 Runtime 内部结构。
- **默认 Policy**：若未指定 policy，自动使用 ReActPolicy(max_steps)，
  符合大多数场景的开箱即用需求。
- **System Prompt**：在 run() 中自动作为第一条 system 消息注入，
  确保 LLM 的 persona 和行为约束在每次执行时一致。
"""

from __future__ import annotations

from typing import Any

from nexus.core.runtime.runtime import Runtime
from nexus.core.executor.react_policy import ReActPolicy
from nexus.core.executor.policy import ExecutionPolicy
from nexus.core.state.types import AgentState
from nexus.llm.base import BaseLLM
from nexus.tools.base import BaseTool
from nexus.plugins.base import Plugin
from nexus.logging import get_logger

logger = get_logger(__name__)


class Agent:
    """Agent 门面类 —— Nexus 框架的顶层入口。

    Agent 封装 Runtime / ToolRegistry / PluginRegistry / EventBus，
    提供"配置 LLM → 注册工具 → 安装插件 → 订阅事件 → 执行任务"的完整流程。

    使用示例
    --------

    >>> from nexus.core.agent.agent import Agent
    >>> from nexus.llm.providers.openai import OpenAIBackend
    >>>
    >>> llm = OpenAIBackend(model="gpt-4")
    >>> agent = Agent(llm=llm, system_prompt="You are a helpful assistant.")
    >>>
    >>> # 注册工具
    >>> agent.register_tool(my_search_tool)
    >>>
    >>> # 安装插件
    >>> await agent.install(my_logging_plugin)
    >>>
    >>> # 执行任务
    >>> state = await agent.run("What is the weather in Beijing?")
    >>> print(state.messages[-1]["content"])

    Attributes
    ----------
    runtime : Runtime
        底层运行时引擎，负责调度循环、状态管理、事件派发。
    llm : BaseLLM
        LLM 实例，用于执行推理调用。
    policy : ExecutionPolicy
        执行策略实例，默认使用 ReActPolicy。
    system_prompt : str or None
        系统提示词（可选），注入为 messages 的第一条 system 消息。
    max_steps : int
        最大执行步数，默认 20 步。
    name : str
        Agent 名称，用于日志和事件追踪。
    tool_registry : ToolRegistry
        工具注册中心的快捷引用。
    events : EventBus
        事件总线的快捷引用。
    plugin_registry : PluginRegistry
        插件注册中心的快捷引用。
    """

    def __init__(
        self,
        llm: BaseLLM,
        policy: ExecutionPolicy | None = None,
        system_prompt: str | None = None,
        max_steps: int = 20,
        name: str = "nexus",
        stream: bool = True,
    ) -> None:
        """初始化 Agent。

        Parameters
        ----------
        llm : BaseLLM
            LLM 实例，用于执行推理调用。必须实现 chat() 和 stream_chat()。
        policy : ExecutionPolicy or None
            执行策略实例。若为 None，默认使用 ReActPolicy(max_steps)，
            满足大多数场景的开箱即用需求。
        system_prompt : str or None
            系统提示词（可选）。若提供，将在每次 run() 时自动注入为
            messages 的第一条 system 消息，确保 LLM 的 persona 和行为约束
            在每次执行时一致。
        max_steps : int
            最大执行步数，传递给 default ReActPolicy 和 Runtime。
            默认为 20 步，防止 LLM 无限循环调用工具。
        name : str
            Agent 名称，用于日志和事件追踪。默认为 "nexus"。
        stream : bool
            是否启用流式输出。默认为 True，Runtime 将调用 stream_chat()
            并逐 chunk 派发 LLM_CHUNK 事件；设为 False 时回退到非流式 chat()。
        """
        self.runtime = Runtime()
        self.llm = llm
        self.policy = policy or ReActPolicy(max_steps=max_steps)
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.name = name
        self._stream = stream

        # 快捷访问：让用户可以直接 agent.tool_registry.register(...) 等方式操作
        self.tool_registry = self.runtime._tool_registry
        self.events = self.runtime._event_bus
        self.plugin_registry = self.runtime._plugin_registry

        logger.info(
            "Agent initialized",
            extra={
                "agent_name": name,
                "llm_type": type(llm).__name__,
                "policy_type": type(self.policy).__name__,
                "max_steps": max_steps,
            },
        )

    def register_tool(self, tool: BaseTool) -> None:
        """注册工具到 Agent。

        将工具实例注册到内部 ToolRegistry。工具注册后，LLM 可通过
        function calling 机制发现并调用该工具。

        Parameters
        ----------
        tool : BaseTool
            待注册的工具实例，必须实现 name、description、schema 属性和
            execute() 方法。

        Raises
        ------
        ValueError
            同名工具已注册时抛出。
        """
        self.tool_registry.register(tool)
        logger.info(
            "Tool registered",
            extra={
                "agent_name": self.name,
                "tool_name": tool.name,
            },
        )

    async def install(self, plugin: Plugin) -> None:
        """安装插件到 Agent。

        执行插件的完整安装流程：
        1. 调用 plugin.install(self)，让插件通过 agent 引用注册工具、
           订阅事件等。
        2. 调用 self.plugin_registry.register(plugin)，将插件加入
           PluginRegistry 并触发 activate()。

        安装完成后插件正式进入"工作中"状态。

        Parameters
        ----------
        plugin : Plugin
            待安装的插件实例。install 方法接收 Agent 引用，可在此方法中
            通过 agent.tool_registry、agent.events 等进行扩展。

        Notes
        -----
        install() 是插件在 Agent 中注册其扩展点的入口。插件的 activate()
        由 PluginRegistry.register() 在 install 后自动调用。
        """
        await plugin.install(self)
        await self.plugin_registry.register(plugin)
        logger.info(
            "Plugin installed",
            extra={
                "agent_name": self.name,
                "plugin_name": plugin.name,
                "plugin_version": plugin.version,
            },
        )

    async def run(
        self,
        task: str,
        variables: dict[str, Any] | None = None,
        initial_messages: list[dict[str, Any]] | None = None,
    ) -> AgentState:
        """执行任务。

        这是 Agent 的主入口方法。内部委托给 Runtime.run() 完成完整的
        执行流程：创建上下文 → 调度循环 → 返回最终状态。

        执行前自动将 self.system_prompt（若配置）作为 system 消息注入到
        initial_messages 中。调用方可传入额外的 initial_messages
        （如之前的对话历史）来保持上下文。

        Parameters
        ----------
        task : str
            任务描述，驱动 Agent 行为的顶层目标。
        variables : dict[str, Any] or None
            运行时变量（可选），存入 state.variables。
        initial_messages : list[dict[str, Any]] or None
            预先填入的对话历史（可选）。会被追加到 system prompt 之后。

        Returns
        -------
        AgentState
            执行完成后的最终状态。
        """
        # 构建 initial_messages：system prompt + 调用方传入的历史
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        if initial_messages:
            messages.extend(initial_messages)

        # 将流式开关注入 variables，供 Runtime._execute_llm_call 读取
        if variables is None:
            variables = {}
        variables["_stream"] = self._stream

        logger.info(
            "Agent.run starting",
            extra={
                "agent_name": self.name,
                "task": task[:100],
            },
        )

        state = await self.runtime.run(
            task=task,
            llm=self.llm,
            policy=self.policy,
            initial_messages=messages or None,
            variables=variables,
            max_steps=self.max_steps,
        )

        logger.info(
            "Agent.run completed",
            extra={
                "agent_name": self.name,
                "run_id": state.run_id,
                "total_steps": state.current_step,
            },
        )

        return state
