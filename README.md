# Nexus Agent Runtime Framework

> 轻量级 · 模型无关 · 插件驱动 · 可扩展 · 可观察的 Agent 运行时框架

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 核心设计理念

Nexus 是一个类似"Agent 操作系统内核"的 Python Core。它的核心设计哲学是**关注点分离**：让 Runtime 只负责最基础、最稳定的调度能力，所有高级能力（模型、工具、策略、插件）通过抽象接口自由替换和扩展。

Nexus 不预设固定的 Agent 模式。无论是简单的 ReAct（思考-行动）循环，还是复杂的多 Agent 协作、规划先行再执行，都可以通过替换或组合不同的组件来实现，而无需修改框架核心。

Core 保持精简稳定，生态通过 Plugin 和 Tool 生长。就像操作系统内核只管理进程调度和资源分配，Nexus Core 只管理 Agent 的生命周期、状态流转和事件派发——其余一切都是可插拔的。

## 架构设计

### 设计思路：Runtime 与 Policy 分离

Nexus 架构的核心决策是将"如何执行"与"下一步做什么"彻底解耦。整个框架由两个正交维度构成：

- **Runtime（运行时引擎）**：负责生命周期管理、状态维护、事件派发、按 Action 指令执行具体操作。它是固定不变的调度循环，不做出任何决策。
- **ExecutionPolicy（执行策略）**：负责根据当前状态决定"下一步该做什么"。它是可替换的决策模块，通过返回 Action 对象来驱动 Runtime。

#### Runtime 与 Policy 分离的原因

传统 Agent 框架通常将 Agent 循环硬编码为某种固定模式，例如：

```
while True:
    response = llm.chat(messages, tools)
    if response.has_tool_calls:
        result = execute_tools(response.tool_calls)
        messages.append(result)
    else:
        return response.content
```

这种"一个循环打天下"的设计导致三个问题：

1. **难以支持不同 Agent 模式**：Planning（规划先行）、Reflection（反思改进）、Multi-Agent（多代理协作）等多种模式需要完全不同的调度逻辑，却被强行塞进同一个循环中，导致代码迅速膨胀为难以维护的 `if-else` 地狱。
2. **模式与核心执行逻辑耦合**：即使只需要一个简单的"先规划后执行"模式，也不得不修改核心调度代码，牵一发而动全身。
3. **测试复杂**：核心循环与 LLM 调用、工具执行紧耦合，每次测试都必须跑完整的循环，单元测试几乎不可能。

Nexus 的解法：

- **Runtime** 只做调度循环、状态管理、事件派发。它的循环是 `while True: get_action_from_policy() -> execute_action()`，不关心是哪种模式。
- **ExecutionPolicy** 只做决策。它是一个只有一个方法 `next_action(context) -> Action` 的接口，纯逻辑不产生副作用。

这意味着你可以在不修改 Runtime 的情况下，通过替换 Policy 实现完全不同的 Agent 模式。比如用一个按固定顺序执行的 RolodexPolicy 来跑审批流程，用 ReflectionPolicy 来做质量审查——Runtime 代码完全不变。

### Action 调度模型

Policy 不直接调用 LLM 或 Tool，而是返回 Action 对象。Runtime 根据 Action 类型执行对应操作。这层间接性带来三个关键好处：

1. **Policy 是纯决策函数**：`next_action(context)` 只读取状态、返回 Action，不执行任何 I/O 操作。这让 Policy 可以独立进行单元测试——传入 mock 的 Context，验证返回的 Action 类型即可。
2. **Runtime 可统一处理横切关注点**：日志记录、事件派发（BEFORE/AFTER）、错误处理、超时控制全部集中在 Runtime 层，Policy 无需关心这些。
3. **未来可支持 Action 序列化/重放**：因为 Action 是纯数据对象，可以序列化后发送到远程执行、写入日志供事后回放、甚至作为分布式调度的工作单元。

当前支持的 Action 类型：

| Action | 含义 | Runtime 行为 |
|--------|------|-------------|
| `LLMCallAction` | 调用 LLM 进行推理 | 发送消息给 LLM，将响应写回 state |
| `ToolCallAction` | 调用工具执行任务 | 通过 ToolExecutor 执行工具，结果写回对话 |
| `PlanAction` | 进入规划阶段 | 驱动 LLM 将任务拆解为步骤序列 |
| `ReflectAction` | 进入反思阶段 | 驱动 LLM 审视已执行步骤并评估 |
| `FinishAction` | 正常终止 | 记录最终结果，退出主循环 |
| `ErrorAction` | 异常终止 | 记录错误信息，退出主循环 |

数据流方向：**Runtime → State → Policy**（单向）。Runtime 修改 State，Policy 只读 State 做决策。所有状态变更集中在 Runtime 中，便于审计和回滚。

### 可扩展性策略

所有高级能力通过插件式接口扩展，Core 保持稳定不变：

- **模型**：通过 `BaseLLM` 抽象，切换 OpenAI / Anthropic / 本地模型只需替换实例。Core 代码不依赖任何具体 Provider。
- **工具**：通过 `BaseTool` 抽象 + `ToolRegistry` 注册，LLM 通过 Function Calling 机制自动发现。两种创建方式：`@tool` 装饰器（声明式）和手动继承（灵活式）。
- **策略**：通过 `ExecutionPolicy` 接口，实现 ReAct / Plan-Execute / Reflection 等不同模式。这是框架最核心的扩展点。
- **插件**：通过 `Plugin` 接口，支持在 Agent 生命周期中注入工具、订阅事件、管理资源。插件拥有完整的 `install → activate → deactivate` 生命周期。
- **事件**：通过 `EventBus` 发布/订阅，覆盖 Agent 执行全过程（8 种事件类型）。handler 异常隔离，不影响主流程。
- **记忆**：通过 `BaseMemory` 抽象，支持短期缓存、向量检索、图记忆等不同存储后端，Core 不依赖任何具体数据库。

## 项目结构

```
nexus/                          # 框架核心包
├── __init__.py                 # 包入口，__version__
├── logging.py                  # 结构化日志模块（NexusFormatter + get_logger）
├── api/                        # 公共 API 层（预留）
│   └── __init__.py
├── core/                       # 运行时核心
│   ├── __init__.py
│   ├── agent/
│   │   ├── __init__.py
│   │   └── agent.py            # Agent 门面类——用户交互的唯一入口
│   ├── context/
│   │   ├── __init__.py
│   │   └── context.py          # ExecutionContext——请求级 DI 容器
│   ├── event/
│   │   ├── __init__.py
│   │   ├── event_bus.py        # EventBus——Async 发布/订阅实现
│   │   ├── event_types.py      # EventType 枚举（8 种事件类型）
│   │   └── types.py            # Event 数据结构
│   ├── exceptions/
│   │   └── __init__.py         # 分层异常体系（NexusError 根 + 7 个子域）
│   ├── executor/
│   │   ├── __init__.py
│   │   ├── actions.py          # Action 类型族（LLMCall / ToolCall / Plan / Reflect / Finish / Error）
│   │   ├── policy.py           # ExecutionPolicy 抽象——最核心的扩展点
│   │   └── react_policy.py     # ReActPolicy——默认的思考-行动交替策略
│   ├── runtime/
│   │   ├── __init__.py
│   │   └── runtime.py          # Runtime——调度引擎：生命周期管理 + 循环调度 + 事件派发
│   └── state/
│       ├── __init__.py
│       └── types.py            # AgentState / Step / ToolCallRecord——可序列化状态快照
├── llm/                        # LLM 抽象层
│   ├── __init__.py
│   ├── base.py                 # BaseLLM / LLMResponse / LLMChunk / ToolCall / UsageStats
│   └── providers/
│       ├── __init__.py
│       └── openai.py           # OpenAI Provider——基于 openai SDK 的实现
├── tools/                      # 工具系统
│   ├── __init__.py
│   ├── base.py                 # BaseTool / ToolResult / ToolError——工具核心抽象
│   ├── builtins.py             # 内置工具：CalculatorTool / EchoTool / CurrentTimeTool
│   ├── decorators.py           # @tool 装饰器——声明式工具元数据注入
│   ├── executor.py             # ToolExecutor——校验→执行→包装→记录 的调度层
│   └── registry.py             # ToolRegistry——工具注册中心（O(1) 查找）
├── plugins/                    # 插件系统
│   ├── __init__.py
│   ├── base.py                 # Plugin 抽象——install/activate/deactivate 生命周期
│   └── registry.py             # PluginRegistry——插件注册与生命周期管理
├── memory/                     # 记忆系统
│   ├── __init__.py
│   ├── base.py                 # BaseMemory / MemoryItem——记忆抽象（预留）
│   └── implementations/
│       └── __init__.py         # 未来具体实现：ShortTerm / Vector / Graph
└── workflow/                   # 工作流编排（预留）
    └── __init__.py

examples/                       # 示例代码
├── __init__.py
├── basic_agent.py              # 最小可运行示例（Hello World）
├── custom_tool.py              # 自定义工具示例（@tool 装饰器 vs 手动继承）
└── custom_policy.py            # 自定义策略示例（RolodexPolicy）

tests/                          # 测试目录
├── __init__.py
├── test_runtime.py             # Runtime 调度循环测试
├── test_react_policy.py        # ReActPolicy 决策逻辑测试
├── test_tools.py               # ToolExecutor + ToolRegistry 测试
├── test_state.py               # AgentState 序列化/反序列化测试
└── test_event_bus.py           # EventBus 发布/订阅测试
```

## 快速开始

### 环境要求

- Python 3.12+

### 安装

```bash
cd d:\Nexus
pip install -e .
```

如果你需要运行测试或开发：

```bash
pip install -e ".[dev]"
```

### 基础用法

创建一个最小可运行的 Agent——使用内置 Mock LLM，无需 API Key：

```python
import asyncio
from nexus.core.agent.agent import Agent
from nexus.tools.builtins import EchoTool

# 使用 MockLLM 或替换为 OpenAILLM
from nexus.llm.providers.openai import OpenAILLM

async def main():
    # 创建 LLM
    llm = OpenAILLM(model="gpt-4o-mini", api_key="sk-xxx")

    # 创建 Agent（默认使用 ReActPolicy）
    agent = Agent(
        llm=llm,
        system_prompt="你是一个友好的助手。",
        max_steps=10,
    )

    # 注册工具
    agent.register_tool(EchoTool())

    # 执行任务
    state = await agent.run("你好，请介绍一下你自己。")

    # 查看结果
    print(state.messages[-1]["content"])

asyncio.run(main())
```

### 自定义工具

两种方式可供选择：

**方式一：`@tool` 装饰器（推荐）**

```python
from nexus.tools.base import BaseTool, ToolResult
from nexus.tools.decorators import tool

@tool(
    name="weather",
    description="查询指定城市的天气信息",
    schema={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称"},
        },
        "required": ["city"],
    },
)
class WeatherTool(BaseTool):
    async def execute(self, args):
        city = args["city"]
        # 调用真实天气 API...
        return ToolResult.ok(data={"city": city, "temp": 25, "condition": "晴"})
```

**方式二：手动继承 `BaseTool`**

```python
class TimeTool(BaseTool):
    @property
    def name(self): return "get_time"

    @property
    def description(self): return "获取当前时间"

    @property
    def schema(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, args):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return ToolResult.ok(data={"iso": now.isoformat()})
```

装饰器方式适合大多数场景（元数据固定、代码简洁）；手动继承适合元数据需要运行时动态计算、或需要管理内部状态（连接池、缓存等）的场景。

### 自定义执行策略

创建一个按固定顺序执行的策略，完全替代默认的 ReActPolicy：

```python
from nexus.core.executor.policy import ExecutionPolicy
from nexus.core.executor.actions import LLMCallAction, ToolCallAction, FinishAction

class FixedStepPolicy(ExecutionPolicy):
    def __init__(self):
        self._phase = 0  # 内部状态：跟踪当前执行阶段

    async def next_action(self, context):
        if self._phase == 0:
            self._phase = 1
            # 第一步：调用 LLM 做初步推理
            return LLMCallAction(
                messages=list(context.state.messages),
                tools=context.tool_executor.registry.to_openai_schemas(),
            )
        elif self._phase == 1:
            self._phase = 2
            # 第二步：强制调用 echo 工具
            return ToolCallAction(
                tool_name="echo",
                tool_call_id="call_fixed_001",
                arguments={"message": "Hello from FixedStepPolicy!"},
            )
        else:
            # 第三步：结束
            return FinishAction(message="固定步骤执行完毕")

# 使用自定义策略
agent = Agent(llm=llm, policy=FixedStepPolicy())
```

Policy 的 `next_action` 应该是纯决策函数——只读取 `context.state`，不修改它。State 的修改由 Runtime 在执行 Action 后完成。

## 日志配置

### 默认日志配置

Nexus 使用 Python 标准库 `logging`，不引入第三方日志依赖。框架内部所有模块通过 `get_logger(__name__)` 获取 logger：

- Logger 名称按模块层级分层，例如 `nexus.core.runtime`、`nexus.llm.providers.openai`、`nexus.tools.executor`，便于按模块精细化控制日志级别。
- 默认使用 `NullHandler`——库代码不强制输出日志，由应用层决定 handler 策略。这意味着如果应用层没有配置 handler，Nexus 不会产生任何日志输出。
- 通过 `NexusFormatter` 将 `LogRecord` 的 `extra` 字段展平为 `key=value` 格式输出，方便追踪 `run_id`、`step`、`tool_name`、`tool_call_id` 等运行时上下文。

### 调整日志级别

```python
import logging

# 只开启 Runtime 的 Debug 日志
logging.getLogger("nexus.core.runtime").setLevel(logging.DEBUG)

# 关闭 LLM Provider 的日志（减少网络调用噪音）
logging.getLogger("nexus.llm.providers").setLevel(logging.WARNING)

# 开启所有 Nexus 模块的 Info 日志
logging.getLogger("nexus").setLevel(logging.INFO)
```

### 接入外部日志系统

```python
import logging
from nexus.logging import NexusFormatter

# 添加 StreamHandler 将日志输出到控制台
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(NexusFormatter())
logging.getLogger("nexus").addHandler(handler)

# 或接入第三方日志系统（如 Loguru、structlog）
# 原理相同：清除 NullHandler，添加自己的 handler
logger = logging.getLogger("nexus")
logger.handlers.clear()
logger.addHandler(your_custom_handler)
```

`NexusFormatter` 的输出格式为：

```
2026-07-29T12:00:00 | nexus.core.runtime | INFO | Agent.run starting | run_id=abc123 agent_name=nexus
```

`extra` 字段的白名单包括：`run_id`、`step`、`tool_name`、`tool_call_id`、`agent_name`、`session_id`、`user_id`、`model`、`provider`。未在白名单中的字段不会出现在日志输出中。

## 扩展指南

### 添加新的 LLM Provider

继承 `BaseLLM`，实现 `chat()` 和 `stream_chat()` 两个方法：

```python
from nexus.llm.base import BaseLLM, LLMResponse, LLMChunk, ToolCall, UsageStats

class GeminiLLM(BaseLLM):
    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None):
        super().__init__()
        self.model = model
        # 初始化 Gemini SDK 客户端...

    async def chat(self, messages, tools=None, **kwargs):
        # 调用 Gemini API，将原生响应转换为 LLMResponse
        ...
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=UsageStats(...),
            model=self.model,
            finish_reason=finish_reason,
        )

    async def stream_chat(self, messages, tools=None, **kwargs):
        # 流式调用，逐个 yield LLMChunk
        ...
        yield LLMChunk(delta_content="...")
```

由于 Agent Core 只依赖 `BaseLLM` 抽象，替换 Provider 不影响任何现有代码：

```python
# 从 OpenAI 切换到 Gemini，只需一行改动
agent = Agent(llm=GeminiLLM(model="gemini-2.0-flash"))
```

### 自定义 Tool

工具的接口约定：

- 必须定义 `name`（snake_case 唯一名称）、`description`（供 LLM 理解何时调用）、`schema`（JSON Schema 格式的参数定义）
- 必须实现 `execute(args) -> ToolResult` 异步方法
- `execute` 内部不应抛出未捕获的异常，使用 `ToolResult.fail(error=...)` 返回错误信息，让 LLM 可以自行解读并纠正
- 可选覆写 `setup()` 和 `teardown()` 管理资源生命周期（如数据库连接池）

工具注册后，LLM 会通过 Function Calling 机制自动发现和调用：

```python
agent.register_tool(MyTool())
# 工具已可用——无需额外配置
```

### 实现自定义 ExecutionPolicy

ExecutionPolicy 是框架最核心的扩展点。不同的 Policy 实现代表不同的 Agent 模式：

| 模式 | 适用场景 | 实现要点 |
|------|---------|---------|
| **ReActPolicy**（默认） | 需要与环境交互的开放式任务 | 根据 LLM 是否返回 tool_calls 动态决定下一步 |
| **PlanAndExecutePolicy** | 多步骤、有明确分解路径的结构化任务 | 先调用 LLM 生成步骤计划，再逐步执行 |
| **ReflectionPolicy** | 需要质量审查和迭代优化的任务 | 执行后让 LLM 反思结果，决定是否调整策略 |
| **自定义 Policy** | 固定审批流程、A/B 测试、多阶段数据处理等 | 按自己的业务逻辑在 `next_action` 中实现状态机 |

实现新 Policy 只需三步：

```python
class MyPolicy(ExecutionPolicy):
    async def next_action(self, context: ExecutionContext) -> Action:
        # 读取 context.state 判断当前阶段
        # 返回对应的 Action 子类实例
        ...
```

实现约定：
- `next_action` 应是**无副作用的纯决策函数**——只读 context，不修改 state
- 返回的 Action 由 Runtime 执行并产生副作用
- 需要终止时返回 `FinishAction` 或 `ErrorAction`，Runtime 收到后会退出主循环

Policy 实例可以在多次 `agent.run()` 之间复用（若无内部状态），也可以在每次 run 时创建新实例（若有内部状态需要重置）。

### 开发 Plugin

Plugin 可以扩展 Agent 的多个维度：注册工具、订阅事件、注入 Memory、扩展 Workflow 等。

```python
from nexus.plugins.base import Plugin
from nexus.core.event.event_types import EventType

class LoggingPlugin(Plugin):
    @property
    def name(self): return "logging-plugin"

    @property
    def version(self): return "1.0.0"

    async def install(self, agent):
        # 在 install 阶段完成"声明式"注册
        agent.events.subscribe(EventType.BEFORE_LLM_CALL, self._on_before_llm)

    async def activate(self):
        # 在 activate 阶段启动后台任务、建立连接等
        pass

    async def deactivate(self):
        # 在 deactivate 阶段释放资源
        pass

    async def _on_before_llm(self, event):
        print(f"[Plugin] LLM 调用即将开始: {event.payload['model']}")

# 安装插件
await agent.install(LoggingPlugin())
```

## API 概览

### Agent（门面类）

用户的唯一入口，封装了 Runtime / ToolRegistry / PluginRegistry / EventBus。

| 方法 | 说明 |
|------|------|
| `__init__(llm, policy, system_prompt, max_steps, name)` | 初始化 Agent。`policy` 默认为 `ReActPolicy`，`max_steps` 默认 20 |
| `await run(task, variables) -> AgentState` | 执行任务，返回最终状态 |
| `register_tool(tool)` | 注册工具到内部 ToolRegistry |
| `await install(plugin)` | 安装插件并激活 |

快捷属性：`agent.tool_registry`、`agent.events`、`agent.plugin_registry`、`agent.runtime`。

### AgentState（运行时状态）

所有执行信息的完整快照，可作为序列化/反序列化的持久化单元。

| 属性 | 说明 |
|------|------|
| `task` | 当前任务描述 |
| `steps` | 历史执行步骤列表（`list[Step]`），含 `step_type`、`input_messages`、`output_content`、`tool_calls` |
| `tool_calls` | 展平的工具调用记录（`list[ToolCallRecord]`） |
| `messages` | 对话消息历史，按 LLM API 格式存储（role + content） |
| `variables` | 运行时变量字典，Policy 可读写 |
| `intermediate_results` | 中间结果字典，含 `final_result` 和 `finish_message` |
| `run_id` | 本次运行的唯一标识 |
| `current_step` | 当前执行步骤计数 |

| 方法 | 说明 |
|------|------|
| `serialize() -> dict` | 序列化为 JSON-able 字典，含嵌套 Step / ToolCallRecord 的递归序列化 |
| `deserialize(data) -> AgentState` | 从字典反序列化恢复，支持缺失字段回退 |
| `add_step(step)` | 追加执行步骤 |
| `add_tool_call(record)` | 追加工具调用记录 |
| `add_message(role, content)` | 追加对话消息 |

### EventBus（事件总线）

Async 发布/订阅模式，8 种事件类型覆盖 Agent 执行全生命周期。

**事件类型：**

| 事件 | 触发时机 | payload 关键字段 |
|------|---------|-----------------|
| `BEFORE_AGENT_RUN` | Agent 执行开始前 | `agent_name`, `session_id`, `run_id` |
| `AFTER_AGENT_RUN` | Agent 执行完成后 | `agent_name`, `result`, `run_id` |
| `BEFORE_LLM_CALL` | LLM 调用前 | `model`, `provider`, `messages`, `tools` |
| `AFTER_LLM_CALL` | LLM 调用后 | `model`, `provider`, `response`, `usage` |
| `BEFORE_TOOL_CALL` | 工具调用前 | `tool_name`, `tool_call_id`, `args` |
| `AFTER_TOOL_CALL` | 工具调用后 | `tool_name`, `tool_call_id`, `result`, `error` |
| `ON_ERROR` | 运行时异常 | `error`, `traceback`, `recoverable`, `run_id` |
| `ON_FINISH` | 运行时完全结束 | `run_id`, `final_state` |

**典型执行顺序：**

```
BEFORE_AGENT_RUN → [BEFORE_LLM_CALL → AFTER_LLM_CALL → BEFORE_TOOL_CALL → AFTER_TOOL_CALL]* → ON_FINISH → AFTER_AGENT_RUN
```

若中途异常，触发 `ON_ERROR`。

| 方法 | 说明 |
|------|------|
| `await subscribe(event_type, handler)` | 订阅事件。handler 可以是同步或异步函数 |
| `await publish(event)` | 发布事件，所有匹配 handler 并发执行，异常隔离 |
| `await unsubscribe(event_type, handler)` | 取消订阅 |

Handler 异常隔离是关键设计：单个 handler 抛出异常不会中断其他 handler 的执行，也不会影响 Agent 主流程。事件监控不应拖垮 Agent 运行。

## 后续路线图

- [ ] 更多 LLM Provider（Anthropic Claude, Google Gemini, 本地模型）
- [ ] Memory 具体实现（短期记忆 / 向量存储 / 图记忆）
- [ ] Workflow 编排（DAG 定义、条件分支、并行执行）
- [ ] FastAPI Server + WebSocket 事件流
- [ ] TypeScript + React Web UI
- [ ] 多 Agent 协作与通信
- [ ] Action 序列化与执行轨迹回放
- [ ] 插件市场与热加载

## CLI 工具

Nexus 提供命令行 Agent 入口 `nexus`，参考 [opencode](https://github.com/sst/opencode) 和 [pi](https://github.com/sst/pi) 的 CLI 体验设计。

### 安装

```bash
# 安装 Nexus + CLI 依赖
pip install -e ".[cli]"

# 验证安装
nexus --help
```

### 快速开始

#### 直接执行模式

```bash
# 设置 API Key（或使用环境变量）
export NEXUS_API_KEY="sk-xxx"

# 一次性问答
nexus "帮我用 Python 写一个快速排序函数"
```

#### 交互式 REPL 模式

```bash
# 进入交互模式
nexus

# 进入后可以多轮对话
> 读取 app.py 的内容
> 分析这段代码的性能问题
> 给我写一个优化版本
> /quit
```

#### 恢复上次会话

```bash
nexus --continue
```

#### 列出历史会话

```bash
nexus --list-sessions
```

### 命令参考

#### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `prompt` | 直接执行的任务描述（位置参数） | - |
| `--model` | LLM 模型名称 | `gpt-4o-mini` |
| `--api-key` | API Key | 环境变量 `NEXUS_API_KEY` |
| `--base-url` | API 自定义端点 | - |
| `--system-prompt` | 系统提示词 | 内置编程助理提示 |
| `--max-steps` | 最大执行步数 | `30` |
| `--work-dir` | 工作目录 | 当前目录 |
| `--continue` | 恢复最近会话 | - |
| `--list-sessions` | 列出历史会话 | - |
| `-v` / `--verbose` | 显示详细日志 | - |
| `--debug` | 显示调试日志 | - |

#### 交互模式内置命令

| 命令 | 说明 |
|------|------|
| `/clear` | 清空对话上下文，保留工具注册 |
| `/save` | 手动保存当前会话 |
| `/tools` | 列出已注册的工具 |
| `/quit` 或 `quit` | 退出 REPL |
| `/help` | 显示帮助 |
| `Ctrl+C` | 中断当前任务（不退出 REPL） |
| `Ctrl+D` | 退出 REPL |

### 配置管理

支持三级配置（优先级从高到低）：

1. **命令行参数**：`--model gpt-4o --api-key sk-xxx`
2. **环境变量**：`NEXUS_MODEL`、`NEXUS_API_KEY`、`NEXUS_BASE_URL`、`NEXUS_MAX_STEPS`
3. **配置文件**：
   - 项目级：`.nexus.json`（当前目录）
   - 用户级：`~/.nexus/config.json`

配置文件示例 (`~/.nexus/config.json`)：

```json
{
  "model": "gpt-4o-mini",
  "provider": "openai",
  "system_prompt": "You are a helpful coding assistant.",
  "max_steps": 30,
  "tools": ["read_file", "write_file", "list_dir"]
}
```

### CLI 内置工具

CLI Agent 默认注册以下文件系统工具：

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `read_file` | 读取文件（含行号，最多 500 行） | `path`, `start_line`, `end_line` |
| `write_file` | 写入/覆盖文件 | `path`, `content` |
| `list_dir` | 列出目录内容 | `path`, `recursive`, `max_depth` |
| `search_content` | 搜索文件内容（grep） | `pattern`, `path`, `file_pattern` |

### 会话管理

- 退出 REPL 时自动保存会话到 `~/.nexus/sessions/`
- `nexus --continue` 恢复最近一次会话
- `nexus --list-sessions` 查看历史会话列表
- 自动截断超长历史（保留最近 50 轮），防止上下文膨胀
