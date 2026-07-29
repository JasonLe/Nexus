# Nexus Agent Runtime Framework Spec

## Why

当前主流 Agent 框架（如 LangChain）往往把 Agent 固化为 `while true: LLM -> Tool -> LLM` 的单一模式，难以优雅地扩展 ReAct / Planning / Reflection / Multi-Agent 等不同执行策略，且 Core 与业务逻辑耦合严重。我们需要一个轻量、模型无关、插件驱动的 Agent Runtime 内核 —— Core 保持稳定只负责最基础的运行能力，模型 / 工具 / Memory / Workflow / Planning 全部可插拔，并为未来的 TypeScript + React Web UI 提供清晰的 API 与事件通信能力。本项目按长期维护的开源项目标准设计，而非一次性 Demo。

## What Changes

### 一、整体架构（首次建立）

采用分层 + 面向接口设计，目录结构如下：

```
nexus/
├── core/                       # 稳定内核，仅基础运行能力
│   ├── agent/                  # Agent 门面类
│   ├── runtime/                # Runtime：生命周期/调度/事件派发
│   ├── state/                  # AgentState 与序列化
│   ├── context/                # ExecutionContext 运行时上下文
│   ├── event/                  # EventBus 与 Event 定义
│   ├── executor/               # Action 与 ExecutionPolicy
│   └── exceptions/             # 异常体系
├── llm/                        # LLM 抽象层
│   ├── base.py
│   └── providers/              # openai/anthropic/gemini/local
├── tools/                      # 插件化工具框架
│   ├── base.py
│   ├── registry.py
│   └── executor.py
├── plugins/                    # VSCode Extension 风格插件
│   ├── base.py
│   └── registry.py
├── memory/                     # Memory 抽象（不绑定具体 DB）
│   ├── base.py
│   └── implementations/
├── workflow/                   # 工作流（后续阶段）
├── api/                        # FastAPI Server（后续阶段）
├── tests/
└── examples/
```

### 二、核心设计决策

- **BREAKING（相对于常规 Agent 框架）**: Agent 不再内置 `while` 循环，而是拆分为 `Runtime` + `ExecutionPolicy`。Runtime 只负责生命周期 / 状态 / 调度 / 事件派发，"下一步做什么"完全由 `ExecutionPolicy.next_action(context) -> Action` 决定。
- **Action-based 调度**: Policy 返回 `Action`（`LLMCallAction` / `ToolCallAction` / `PlanAction` / `ReflectAction` / `FinishAction` / `ErrorAction`），Runtime 根据 Action 类型执行对应操作。
- **面向接口**: `BaseLLM` / `BaseTool` / `BaseMemory` / `Plugin` 全部基于抽象基类，Core 不依赖任何具体实现。
- **Async 优先**: 所有 I/O 接口（LLM / Tool / Memory / 事件）使用 `async/await`。
- **事件驱动**: 全流程 8 类事件可订阅，为未来 Web UI 流式展示思考/工具调用过程打基础。
- **可序列化 State**: AgentState 可序列化保存与反序列化恢复，为未来"暂停/恢复 Agent 任务"打基础。
- **结构化日志**: 全流程使用 Python 标准 `logging` + 结构化字段（logger 名按模块分层 `nexus.core.runtime` / `nexus.llm.providers.openai` 等），关键节点（Runtime 启动/结束、Action 执行、LLM/Tool 调用、事件派发、错误）必须打日志，含 run_id / step 序号 / 工具名等上下文，便于排查与未来 UI 展示。
- **注释即设计文档**: 每个模块/类/关键方法必须含 docstring 说明「职责 / 设计思路 / 为何这样设计」，复杂逻辑处补行内注释解释意图（而非复述代码）。公共抽象类 docstring 须说明扩展点与实现约定，让第三方开发者无需读源码即可正确扩展。

### 三、模块职责 / 类关系 / 数据流 / 执行流程

#### 模块职责
| 模块 | 职责 |
|---|---|
| `core/agent` | Agent 门面类，组装 LLM/Tools/Memory/Policy，对外暴露 `run()` / `install()` |
| `core/runtime` | Runtime：管理生命周期、调度 Action 执行、维护 State、派发 Event |
| `core/state` | AgentState 数据结构与序列化/反序列化 |
| `core/context` | ExecutionContext：封装单次运行的 state/llm/tools/memory/events/variables |
| `core/event` | EventType 枚举 + Event dataclass + EventBus（async pub/sub） |
| `core/executor` | Action 类型族 + ExecutionPolicy 抽象 + 默认 ReAct Policy |
| `core/exceptions` | NexusError 异常体系 |
| `llm` | BaseLLM 抽象 + providers 实现 |
| `tools` | BaseTool + `@tool` 装饰器 + ToolRegistry + ToolExecutor |
| `plugins` | Plugin 抽象 + PluginRegistry |
| `memory` | BaseMemory 抽象（save/search/delete/forget） |

#### 类关系（核心）
```
Agent ── owns ──> Runtime, ToolRegistry, PluginRegistry, EventBus
Runtime ── uses ──> ExecutionPolicy, ExecutionContext, EventBus, AgentState
ExecutionPolicy.next_action(ExecutionContext) -> Action
Runtime.execute(Action) ── calls ──> BaseLLM / ToolExecutor
ToolExecutor ── looks up ──> ToolRegistry ── holds ──> BaseTool
BaseLLM / BaseTool / BaseMemory / Plugin ── all abstract, pluggable
```

#### 数据流
```
用户输入
  -> Agent.run(input)
  -> Runtime 启动，创建 ExecutionContext + AgentState
  -> 循环:
       Policy.next_action(context) -> Action
       Runtime 执行 Action (LLM 调用 / Tool 调用 / 结束 / 错误)
       Runtime 派发对应 Event
       Runtime 更新 AgentState (steps / tool_calls / variables)
       若 FinishAction 或 ErrorAction -> 退出循环
  -> 返回最终结果
```

#### 执行流程（ReAct 默认策略示例）
```
1. Policy 返回 LLMCallAction（携带用户输入）
2. Runtime 调用 LLM，派发 BeforeLLMCall/AfterLLMCall
3. LLM 返回 tool_calls -> Policy 返回 ToolCallAction
4. Runtime 调用 ToolExecutor，派发 BeforeToolCall/AfterToolCall
5. Tool 结果回写 State -> 回到步骤 1
6. LLM 返回 finish -> Policy 返回 FinishAction -> Runtime 派发 OnFinish，结束
```

### 四、MVP 范围（本次交付）

1. Agent Runtime（Runtime + ExecutionContext + Action 调度循环）
2. LLM 抽象 + OpenAI 实现
3. Tool 系统（base + registry + executor + `@tool` 装饰器 + 1 个示例工具）
4. State 系统（AgentState，可序列化/反序列化）
5. Event 系统（EventBus + 8 类事件）
6. 默认 ReAct ExecutionPolicy

### 五、后续阶段（不在本次 MVP，仅预留接口）

- Memory 具体实现（Vector / Graph）、完整 Plugin 系统、Workflow 模块
- FastAPI Server（POST /agent/run、GET /agent/state/{id}、GET /agent/events/{id} SSE、WS /agent/stream/{id}）
- 其他 LLM Provider（Anthropic / Gemini / Local）
- TypeScript + React Web UI 对接

## Impact
- Affected specs: 无（全新项目）
- Affected code: 全新仓库 `d:\Nexus`，按上述目录结构建立

## ADDED Requirements

### Requirement: 模块化内核结构
The system SHALL 组织为高内聚低耦合的模块，Core 仅包含 Agent 最基础运行能力，高级能力通过 plugins/tools 扩展，每个核心模块提供 `base.py` 抽象接口。

#### Scenario: 目录分层
- **WHEN** 开发者查看项目
- **THEN** 应看到 `core/ llm/ tools/ plugins/ memory/ workflow/ api/ tests/ examples/` 分层
- **AND** 每个核心模块有清晰的 `base.py` 抽象接口

### Requirement: Agent Runtime + ExecutionPolicy 分离
The system SHALL 将 Agent 执行拆分为 Runtime（生命周期/状态/调度/事件派发）与 ExecutionPolicy（决定下一步动作），Agent 不内置固定循环。

#### Scenario: 自定义执行策略
- **WHEN** 开发者实现 `ExecutionPolicy.next_action(context)` 返回 Action
- **THEN** Runtime 应根据 Action 类型执行对应操作（LLM 调用 / Tool 调用 / 结束 / 错误）
- **AND** 不修改 Runtime 即可支持 ReAct / Planning / Reflection 等不同模式

#### Scenario: 默认 ReAct 策略
- **WHEN** 创建 Agent 时未指定 Policy
- **THEN** 系统应提供默认 ReAct 风格 ExecutionPolicy

#### Scenario: Action 类型完整
- **WHEN** Policy 决定下一步
- **THEN** 可返回 LLMCallAction / ToolCallAction / PlanAction / ReflectAction / FinishAction / ErrorAction 之一

### Requirement: LLM 抽象层
The system SHALL 提供统一 LLM 接口，Agent Core 不依赖具体模型实现，支持 OpenAI/Anthropic/Gemini/本地模型可插拔。

#### Scenario: 模型切换
- **WHEN** 开发者将 Agent 的 LLM 从 OpenAI 切换为 Anthropic
- **THEN** Agent 与 Runtime 代码无需修改，仅替换 LLM 实例

#### Scenario: 流式与 Tool Calling 与 Token 统计
- **WHEN** 调用 `BaseLLM.chat()` 或 `stream_chat()`
- **THEN** 应支持 streaming、tool calling，并返回 token 使用统计（UsageStats）

### Requirement: 插件化 Tool 系统
The system SHALL 提供 Tool 框架，工具具备 name/description/schema/execute，支持 `@tool` 装饰器、动态注册与发现、Agent 运行时调用。

#### Scenario: 工具注册与调用
- **WHEN** 开发者用 `@tool` 装饰器定义 SearchTool 并注册到 ToolRegistry
- **THEN** Agent 在执行过程中能通过 ToolExecutor 发现并调用该工具，结果回写 State

#### Scenario: schema 校验
- **WHEN** ToolExecutor 执行工具
- **THEN** 应根据工具 schema 校验入参，校验失败抛出 ToolError

### Requirement: 可序列化 State 系统
The system SHALL 维护 AgentState（当前任务/历史步骤/Tool 调用记录/Memory 引用/中间结果/Variables），支持序列化保存与反序列化恢复执行。

#### Scenario: 暂停恢复
- **WHEN** Agent 执行中途序列化 State
- **THEN** 应能在新进程反序列化并继续执行

#### Scenario: State 字段完整
- **WHEN** 查看 AgentState
- **THEN** 应包含 task / steps / tool_calls / memory_refs / intermediate_results / variables 字段

### Requirement: 事件系统
The system SHALL 提供 EventBus，支持 BeforeAgentRun / AfterAgentRun / BeforeLLMCall / AfterLLMCall / BeforeToolCall / AfterToolCall / OnError / OnFinish 八类事件订阅与派发，支持同步与 async handler。

#### Scenario: 订阅事件
- **WHEN** 开发者订阅 `BeforeToolCall`
- **THEN** 每次工具调用前应触发回调，payload 包含工具名与参数

#### Scenario: 错误事件
- **WHEN** 任意环节抛出异常
- **THEN** Runtime 应派发 OnError 事件并携带异常信息

### Requirement: Async 优先与类型提示
The system SHALL 全部 I/O 接口使用 async/await，所有公开 API 使用 Python 3.12+ 类型提示，遵循 SOLID 原则。

### Requirement: 结构化日志
The system SHALL 在全流程关键节点使用 Python 标准 `logging` 输出结构化日志，logger 名按模块分层（如 `nexus.core.runtime`、`nexus.llm.providers.openai`），日志须包含 run_id / step 序号 / 工具名等上下文字段，便于排查与未来 UI 展示。

#### Scenario: 关键节点日志
- **WHEN** Runtime 启动 / 结束、执行 Action、调用 LLM / Tool、派发事件、捕获错误
- **THEN** 必须输出对应级别（INFO/DEBUG/WARNING/ERROR）日志，含 run_id 与上下文字段

#### Scenario: 模块化 logger
- **WHEN** 开发者查看日志
- **THEN** logger 名应反映模块归属（`nexus.core.runtime` / `nexus.tools.executor` 等），可按模块调整日志级别

### Requirement: 注释即设计文档
The system SHALL 在每个模块/类/关键方法编写 docstring 说明「职责 / 设计思路 / 为何这样设计」，复杂逻辑处补行内注释解释意图，公共抽象类 docstring 须说明扩展点与实现约定，使第三方开发者无需读源码即可正确扩展。

#### Scenario: 抽象类扩展点说明
- **WHEN** 第三方开发者阅读 `BaseLLM` / `BaseTool` / `ExecutionPolicy` / `BaseMemory` / `Plugin`
- **THEN** docstring 应说明：该抽象的职责、需要实现哪些方法、每个方法的入参出参与约定、调用时机

#### Scenario: 关键方法设计思路
- **WHEN** 阅读 `Runtime` 调度循环、`ReActPolicy.next_action`、`ToolExecutor.execute` 等关键方法
- **THEN** docstring 应说明设计思路与决策原因，复杂分支处有行内注释解释意图

### Requirement: 可扩展插件机制（接口预留）
The system SHALL 预留 Plugin 接口（name/version/install/activate/deactivate）与 BaseMemory 抽象（save/search/delete/forget），允许插件扩展 Memory/Tool/Event/Workflow。

#### Scenario: 安装插件
- **WHEN** 调用 `agent.install(BrowserPlugin())`
- **THEN** 插件应能注册工具、订阅事件、扩展 Memory

### Requirement: 测试与示例
The system SHALL 提供单元测试（event/state/tool/policy/runtime）与可独立运行的示例代码（basic_agent / custom_tool / custom_policy）及 README。
