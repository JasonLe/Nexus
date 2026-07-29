# Checklist

## 架构与抽象

- [x] 目录结构符合 `core/ llm/ tools/ plugins/ memory/ workflow/ api/ tests/ examples/` 分层
- [x] 每个核心模块（core/llm/tools/plugins/memory）有 `base.py` 抽象接口
- [x] Core 不依赖任何具体 LLM / Tool / Memory 实现（仅依赖抽象基类）
- [x] Agent 不内置 while-loop，拆分为 Runtime + ExecutionPolicy
- [x] 定义 Action 类型族（LLMCall / ToolCall / Plan / Reflect / Finish / Error）
- [x] 定义 ExecutionContext 封装运行时上下文（state/llm/tools/memory/events/variables）
- [x] 定义核心异常体系（NexusError + 6 个子类）

## LLM 抽象

- [x] `BaseLLM` 提供 `chat` / `stream_chat` 抽象方法（async）
- [x] 支持 `tools` 参数（tool calling）
- [x] 返回 token 使用统计（`UsageStats`）
- [x] `llm/providers/openai.py` 实现完整 OpenAI Provider
- [x] Agent Core 不 import 任何 openai 专属类型

## Tool 系统

- [x] `BaseTool` 定义 name / description / schema / `async execute(args)`
- [x] `@tool` 装饰器可用
- [x] `ToolRegistry` 支持 register / get / list / to_openai_schemas
- [x] `ToolExecutor` 支持 schema 校验 + 执行 + 错误处理
- [x] 提供至少 1 个内置示例工具

## State 系统

- [x] `AgentState` 包含 task / steps / tool_calls / memory_refs / intermediate_results / variables
- [x] `Step` 与 `ToolCallRecord` 数据结构完整
- [x] `serialize()` 输出 JSON-able dict
- [x] `deserialize()` 可恢复完整 State
- [x] 序列化往返一致（round-trip）

## Event 系统

- [x] `EventType` 枚举包含 8 类事件（BeforeAgentRun/AfterAgentRun/BeforeLLMCall/AfterLLMCall/BeforeToolCall/AfterToolCall/OnError/OnFinish）
- [x] `EventBus` 支持 async subscribe / publish
- [x] 同时支持同步与 async handler
- [x] handler 异常被隔离，不阻断主流程
- [x] Runtime 在对应生命周期点派发事件

## Runtime & Agent

- [x] `Runtime` 负责生命周期 / 状态 / 调度 / 事件派发
- [x] `Runtime` 根据 Action 类型执行对应操作
- [x] `Agent` 门面类提供 `run(input)` / `install(plugin)` API
- [x] 默认 ReAct ExecutionPolicy 可用
- [x] 支持 max_steps 兜底防止无限循环

## 代码质量

- [x] `pyproject.toml` 声明 Python 3.12+ 依赖
- [x] 所有公开 API 使用类型提示
- [x] 所有 I/O 接口使用 async/await
- [x] 遵循 SOLID 原则（特别是依赖倒置：Core 依赖抽象）
- [x] 代码含清晰中文/英文注释

## 日志

- [x] `nexus/logging.py` 提供 `get_logger(name)` 工厂与结构化 formatter
- [x] 各模块使用 `get_logger(__name__)` 获取 logger，logger 名按模块分层
- [x] Runtime 启动/结束、Action 执行、LLM/Tool 调用、事件派发、错误等关键节点有日志
- [x] 日志 extra 字段含 run_id / step 序号 / 工具名等上下文
- [x] 日志级别合理（INFO 关键节点 / DEBUG 细节 / WARNING 可恢复 / ERROR 错误）

## 注释与设计文档

- [x] 每个模块/类/关键方法含 docstring 说明「职责 / 设计思路 / 为何这样设计」
- [x] 复杂逻辑处有行内注释解释意图（而非复述代码）
- [x] `BaseLLM` / `BaseTool` / `ExecutionPolicy` / `BaseMemory` / `Plugin` 抽象类 docstring 说明扩展点与实现约定
- [x] Runtime 调度循环、ReAct Policy 状态转移、ToolExecutor 校验流程等关键决策处记录设计原因
- [x] 第三方开发者无需读源码即可根据 docstring 正确扩展

## 测试与示例

- [x] 单元测试覆盖 event / state / tools / policy / runtime
- [x] 示例代码可独立运行（basic_agent / custom_tool / custom_policy）
- [x] README 包含架构说明与快速开始

## 可扩展性（接口预留）

- [x] `Plugin` 接口预留（name/version/install/activate/deactivate）
- [x] `PluginRegistry` 可注册与查询插件
- [x] `BaseMemory` 抽象预留（save/search/delete/forget）
- [x] 未来可新增 LLM Provider 而不改 Core
- [x] 未来可新增 ExecutionPolicy 而不改 Runtime
