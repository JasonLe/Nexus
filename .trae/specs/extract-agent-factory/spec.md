# 抽取 Agent 工厂模块 Spec

## Why

当前 Agent 的组装逻辑（创建 LLM + 构造 Agent + 注册工具）散落在 `nexus/cli/main.py` 的私有函数 `_create_llm` / `_register_tools` 中，被 `nexus/server/app.py` 和 `nexus/cli/repl.py` 反向 import 复用。这导致：

1. 每加一个新 provider 或新工具，需要改 `cli/main.py`，但 server 和 repl 也间接依赖，容易遗漏同步
2. `server/app.py` 的 `PUT /api/config` 需要手写一遍重建逻辑（已发生过模型切换不生效的 bug）
3. CLI 模块承担了本应属于核心层的工厂职责，违反单一职责

目标：把 Agent 组装逻辑抽到核心层的独立工厂模块，CLI / Server / REPL 三个调用方都只调一个入口函数，实现"加能力只改一处"。

## What Changes

- **新增** `nexus/core/factory.py`：提供 `create_agent(config) -> Agent` 单一入口，内部封装 LLM 创建 + Agent 构造 + 工具注册
- **迁移** `nexus/cli/main.py` 的 `_create_llm` / `_register_tools` 逻辑到 factory 模块（保留 `main.py` 内的薄包装以兼容 CLI 参数覆盖场景）
- **修改** `nexus/server/app.py`：`create_app()` 和 `PUT /api/config` 的重建逻辑改为调用 `create_agent(config)`
- **修改** `nexus/cli/repl.py`：`/clear` 命令的 Agent 重建改为调用 factory
- **删除** `server/app.py` 和 `repl.py` 对 `cli/main.py` 私有函数的反向 import

## Impact

- Affected specs: `build-nexus-agent-runtime`（Agent 构造方式变更）, `optimize-cli-and-add-desktop-ui`（Server 适配层简化）
- Affected code:
  - `nexus/core/factory.py`（新建）
  - `nexus/cli/main.py`（_create_llm / _register_tools 迁出，保留 CLI 入口）
  - `nexus/server/app.py`（create_app + put_config 简化）
  - `nexus/cli/repl.py`（/clear 重建逻辑简化）
  - `tests/test_server_api.py`（验证配置切换重建）
  - `tests/test_runtime.py`（若涉及 Agent 构造）

## ADDED Requirements

### Requirement: Agent 工厂模块

系统 SHALL 提供一个核心层工厂模块 `nexus.core.factory`，导出单一入口函数 `create_agent(config: NexusConfig) -> Agent`，封装以下完整组装流程：

1. 根据 `config.default_provider` 和 `config.provider_config` 创建对应 LLM 实例
2. 根据 `config.system_prompt` / `config.max_steps` / `config.stream` 构造 Agent
3. 根据 `config.tools.enabled` 注册工具（空列表 = 全部内置工具）

#### Scenario: 默认配置创建 Agent

- **WHEN** 调用 `create_agent(config)`，config.default_provider="openai"，config.tools.enabled=[]
- **THEN** 返回的 Agent 使用 OpenAI LLM，注册全部内置工具

#### Scenario: 指定 provider 创建 Agent

- **WHEN** 调用 `create_agent(config)`，config.default_provider="minimax"
- **THEN** 返回的 Agent 使用 MiniMax LLM

#### Scenario: 工具过滤

- **WHEN** config.tools.enabled=["read_file", "shell"]
- **THEN** Agent 只注册 read_file 和 shell 两个工具

### Requirement: 配置变更后即时重建

系统 SHALL 在 `PUT /api/config` 保存配置后，调用 `create_agent(config)` 重建 Agent 实例并替换 `app.state.agent`，使新 provider / model / api_key 立即生效，无需重启服务。

#### Scenario: 切换 provider 立即生效

- **WHEN** 通过 PUT /api/config 把 default_provider 从 minimax 改为 anthropic 并保存
- **THEN** 后续 WebSocket 聊天请求使用 Anthropic LLM，不再使用 minimax

## MODIFIED Requirements

### Requirement: CLI 入口组装 Agent

`nexus/cli/main.py` 的 `main()` 函数 SHALL 通过调用 `nexus.core.factory.create_agent(config)` 创建 Agent，而非自己拼装 LLM + Agent + 工具。CLI 的命令行参数覆盖（`--model` / `--provider` 等）通过先修改 config 对象再传入 factory 实现。

### Requirement: Server 应用工厂

`nexus/server/app.py` 的 `create_app()` SHALL 通过调用 `nexus.core.factory.create_agent(config)` 创建 Agent，不再从 `nexus.cli.main` import `_create_llm` / `_register_tools`。

### Requirement: REPL /clear 重建

`nexus/cli/repl.py` 的 `/clear` 命令 SHALL 调用 `create_agent(config)` 重建 Agent，不再从 `nexus.cli.main` import `_register_tools`。
