# Tasks

## 阶段一：项目骨架与抽象接口（可并行）

- [x] Task 1: 建立项目骨架与开发配置
  - [x] SubTask 1.1: 创建 `pyproject.toml`（Python 3.12+，运行依赖：openai/httpx/pydantic，dev 依赖：pytest/pytest-asyncio/ruff/mypy；MVP 不强制 fastapi）
  - [x] SubTask 1.2: 建立完整目录结构 `core/{agent,runtime,state,context,event,executor,exceptions}`、`llm/providers`、`tools`、`plugins`、`memory/implementations`、`workflow`、`api`、`tests`、`examples`，各含 `__init__.py`
  - [x] SubTask 1.3: 创建顶层 `nexus/__init__.py` 暴露公开 API
  - [x] SubTask 1.4: 在 `nexus/logging.py` 建立统一日志配置：`get_logger(name)` 工厂、结构化 formatter（含 run_id/step/工具名等 extra 字段）、默认配置 + NullHandler，供各模块 `logger = get_logger(__name__)` 使用
- [x] Task 2: 定义核心异常体系
  - [x] SubTask 2.1: 在 `core/exceptions/__init__.py` 定义 `NexusError` 基类及子类：`LLMError` / `ToolError` / `StateError` / `PolicyError` / `AgentRuntimeError` / `PluginError` / `EventError`
- [x] Task 3: 定义事件系统抽象
  - [x] SubTask 3.1: 在 `core/event/` 定义 `EventType` 枚举（8 类）、`Event` dataclass（type/payload/timestamp）、`EventBus` 抽象（subscribe/publish，async）
- [x] Task 4: 定义状态系统数据结构
  - [x] SubTask 4.1: 在 `core/state/` 定义 `Step` / `ToolCallRecord` dataclass
  - [x] SubTask 4.2: 定义 `AgentState`（task/steps/tool_calls/memory_refs/intermediate_results/variables），含 `serialize()` / `deserialize()` 方法完整实现
- [x] Task 5: 定义执行上下文与 Action 体系
  - [x] SubTask 5.1: 在 `core/context/` 定义 `ExecutionContext`（state/llm/tools/memory/events/variables/max_steps）
  - [x] SubTask 5.2: 在 `core/executor/` 定义 Action 类型族：`LLMCallAction` / `ToolCallAction` / `PlanAction` / `ReflectAction` / `FinishAction` / `ErrorAction`（基类 `Action`）
  - [x] SubTask 5.3: 定义 `ExecutionPolicy` 抽象基类（`async next_action(context) -> Action`）
- [x] Task 6: 定义 LLM 抽象层
  - [x] SubTask 6.1: 在 `llm/base.py` 定义 `Message` / `LLMResponse` / `LLMChunk` / `UsageStats` / `ToolCall` dataclass
  - [x] SubTask 6.2: 定义 `BaseLLM` 抽象（`async chat()` / `async stream_chat()`，支持 tools 参数）
- [x] Task 7: 定义 Tool 系统抽象
  - [x] SubTask 7.1: 在 `tools/base.py` 定义 `BaseTool` 抽象（name/description/schema/`async execute(args)`）与 `ToolResult` dataclass
  - [x] SubTask 7.2: 定义 `@tool` 装饰器（将类标记为工具并自动注册元数据）
  - [x] SubTask 7.3: 在 `tools/registry.py` 定义 `ToolRegistry`（register/get/list/to_openai_schemas）
  - [x] SubTask 7.4: 在 `tools/executor.py` 定义 `ToolExecutor`（校验 + 执行 + 记录）
- [x] Task 8: 定义 Memory 与 Plugin 抽象（接口预留）
  - [x] SubTask 8.1: 在 `memory/base.py` 定义 `MemoryItem` dataclass 与 `BaseMemory` 抽象（save/search/delete/forget）
  - [x] SubTask 8.2: 在 `plugins/base.py` 定义 `Plugin` 抽象（name/version/install/activate/deactivate）与 `PluginRegistry`

## 阶段二：MVP 实现

> 横切要求（适用于本阶段所有 Task）：
> - **日志**：每个模块用 `get_logger(__name__)` 获取 logger，在关键节点（启动/结束/Action 执行/LLM/Tool 调用/事件派发/错误）打日志，extra 字段含 run_id / step / 工具名
> - **注释**：每个模块/类/方法含 docstring 说明「职责 / 设计思路 / 为何这样设计」，复杂逻辑补行内注释解释意图；抽象类 docstring 须说明扩展点与实现约定
> - **设计思路**：在关键决策处（如 Runtime 循环结构、ReAct Policy 状态转移、ToolExecutor 校验流程）用 docstring 记录设计原因，便于长期维护

- [x] Task 9: 实现 EventBus
  - [x] SubTask 9.1: 在 `core/event/event_bus.py` 实现 async `publish` / `subscribe`，同时支持同步与 async handler，handler 异常隔离不阻断主流程
- [x] Task 10: 实现 OpenAI LLM Provider
  - [x] SubTask 10.1: 在 `llm/providers/openai.py` 实现 `OpenAILLM`（基于 openai SDK，`chat` + `stream_chat`，支持 tool calling 与 UsageStats 统计）
- [x] Task 11: 实现 ToolExecutor 与内置示例工具
  - [x] SubTask 11.1: 在 `tools/executor.py` 实现完整 `ToolExecutor`（schema 校验 + 执行 + 错误处理 + 返回 ToolResult）
  - [x] SubTask 11.2: 在 `tools/builtins.py` 实现 3 个示例工具（`CalculatorTool` / `EchoTool` / `CurrentTimeTool`）便于测试与示例
- [x] Task 12: 实现默认 ReAct ExecutionPolicy
  - [x] SubTask 12.1: 在 `core/executor/react_policy.py` 实现 ReAct 风格 `next_action`：首次返回 LLMCallAction；LLM 返回 tool_calls 时返回 ToolCallAction；无 tool_calls 时返回 FinishAction；并支持 max_steps 兜底
- [x] Task 13: 实现 Agent Runtime
  - [x] SubTask 13.1: 在 `core/runtime/runtime.py` 实现 `Runtime`：调度循环、根据 Action 类型执行（LLM/Tool/Finish/Error）、派发 8 类事件、更新 State、错误处理
  - [x] SubTask 13.2: 在 `core/agent/agent.py` 实现 `Agent` 门面类：组装 llm/tools/policy/memory，暴露 `run(input)` / `install(plugin)` / 状态访问
- [x] Task 14: 实现 AgentState 序列化与恢复
  - [x] SubTask 14.1: 完整实现 `AgentState.serialize()` 输出 JSON-able dict 与 `AgentState.deserialize()` 恢复，含所有子结构

## 阶段三：测试与示例

- [x] Task 15: 编写单元测试
  - [x] SubTask 15.1: `tests/test_event_bus.py` 事件订阅/派发/异常隔离
  - [x] SubTask 15.2: `tests/test_state.py` 序列化/反序列化往返一致
  - [x] SubTask 15.3: `tests/test_tools.py` 注册/发现/schema 校验/执行
  - [x] SubTask 15.4: `tests/test_react_policy.py` 用 Mock LLM 验证 Action 序列
  - [x] SubTask 15.5: `tests/test_runtime.py` 集成测试：Mock LLM + Tool 跑通完整流程
- [x] Task 16: 编写示例代码
  - [x] SubTask 16.1: `examples/basic_agent.py` 最小可用 Agent（Mock LLM + EchoTool）
  - [x] SubTask 16.2: `examples/custom_tool.py` 自定义 `@tool` 工具
  - [x] SubTask 16.3: `examples/custom_policy.py` 自定义 ExecutionPolicy
- [x] Task 17: 编写 README
  - [x] SubTask 17.1: 项目介绍 / 架构图（模块职责） / 快速开始 / 扩展指南（自定义 LLM/Tool/Policy/Plugin）
  - [x] SubTask 17.2: 日志配置说明（如何调整级别、结构化字段、接入外部日志系统）与设计思路章节（Runtime+Policy 拆分原因、Action 调度模型、可扩展性策略）

# Task Dependencies

- Task 1（含 1.4 日志配置）是所有后续 Task 的前置
- Task 2 / 3 / 4 / 5 / 6 / 7 / 8 互相独立，可并行（均为抽象定义）
- Task 9 依赖 Task 3 + Task 1.4
- Task 10 依赖 Task 6 + Task 1.4
- Task 11 依赖 Task 7 + Task 1.4
- Task 12 依赖 Task 5 + Task 1.4
- Task 13 依赖 Task 9 / 11 / 12（Runtime 需要 EventBus、ToolExecutor、Policy）
- Task 14 依赖 Task 4
- Task 15 依赖 Task 13 / 14
- Task 16 / 17 依赖 Task 15
