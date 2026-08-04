# MCP Client 支持 Spec

## Why

MCP（Model Context Protocol）是业界事实标准的工具协议（roadmap M1.1，P0 生态缺口）。一次接入即可获得全部 MCP 生态工具（filesystem / git / github / postgres / puppeteer 等数千个 server），同时为 CLI 与 Desktop 用户提供统一的 MCP 工具安装、使用与卸载能力。

## What Changes

- 新增 `nexus/tools/mcp/` 子包：`MCPClient`（stdio + Streamable HTTP 传输）、`MCPToolAdapter`（把远端 MCP tool 适配为 `BaseTool`，schema 透传，结果包装为 `ToolResult`）、`MCPManager`（连接与工具生命周期管理）
- 接入方式遵循项目理念：实现为官方 `MCPPlugin`——install 阶段连接各 server、拉取工具列表、注册适配器；deactivate 阶段断开。**不修改 Runtime**
- `nexus.yaml` 新增 `mcp_servers` 配置节（name / command / args / env / url / enabled），走既有五级配置合并体系，`generate_config_template()` 与 `save_config()` 同步更新
- 依赖：官方 `mcp` Python SDK 作为可选 extra（`pip install -e ".[mcp]"`），未安装时优雅降级（跳过 MCP 连接并记日志，不影响其他能力）
- CLI：REPL 新增 `/mcp` 命令族（list / tools / add / remove / enable / disable / reconnect），`/tools` 输出区分内置工具与 MCP 工具
- Server：新增 `/api/mcp` REST 契约（查询 / 安装 / 更新 / 开关 / 卸载 / 重连），变更持久化到配置文件并重建 Agent 生效；`GET /api/tools` 返回工具来源（builtin / mcp）与所属 server
- Desktop：新增 MCP 管理视图（server 列表、状态、安装/编辑表单、开关、卸载、查看工具），ToolsView 按「内置工具 / MCP 工具」分组展示
- 工具命名：MCP 工具统一以 `mcp__<server>__<tool>` 前缀注册（Claude Code 风格），避免与内置工具及跨 server 命名冲突

## Impact

- Affected specs: 工具系统（tools）、配置体系（cli/config）、CLI REPL 命令、Server API 契约、Desktop UI
- Affected code:
  - 新增：`nexus/tools/mcp/`（client.py / adapter.py / manager.py / plugin.py）
  - 修改：`nexus/cli/config.py`（mcp_servers 解析/保存/模板）、`nexus/core/factory.py`（create_agent 挂载 MCPPlugin）、`nexus/cli/repl.py`（/mcp 命令）、`nexus/cli/display.py`（渲染）、`nexus/server/app.py`（/api/mcp 路由、/api/tools 来源标注）、`pyproject.toml`（mcp extra）
  - Desktop：`desktop/src/api/{client.ts,types.ts}`、新增 `views/McpView.tsx`、修改 `SideNav.tsx` / `App.tsx` / `views/ToolsView.tsx` / `store/appStore.ts`
  - 测试：`tests/test_mcp_*.py`

## ADDED Requirements

### Requirement: MCP Server 配置
系统 SHALL 在 `nexus.yaml` 支持 `mcp_servers` 配置节，每个 server 支持两种传输方式：stdio（`command` + `args` + 可选 `env`）与 Streamable HTTP（`url`），并支持 `enabled: false` 停用。配置走既有五级合并体系，`save_config()` 与 `generate_config_template()` 同步覆盖该节。

#### Scenario: 配置加载
- **WHEN** 用户在 `nexus.yaml` 中声明 `mcp_servers`（如 filesystem stdio server）
- **THEN** `load_config()` 将其解析为 `config.mcp_servers`，字段含 name / transport / command / args / env / url / enabled

#### Scenario: 非法配置容错
- **WHEN** 某 server 配置缺少 command 与 url（无法确定传输方式）
- **THEN** 加载时跳过该 server 并记 warning 日志，不影响其余配置加载

### Requirement: MCP 工具接入（MCPToolAdapter + MCPPlugin）
系统 SHALL 通过官方 `MCPPlugin` 将各 MCP server 的工具适配为 `BaseTool` 并注册进 `ToolRegistry`：schema 透传远端定义，执行结果包装为 `ToolResult`；工具名统一为 `mcp__<server>__<tool>`。插件 deactivate 时断开全部连接。Runtime 零修改。

#### Scenario: 工具注册与调用
- **WHEN** Agent 启动且配置了已启用、可达的 MCP server
- **THEN** 该 server 的工具以 `mcp__<server>__<tool>` 名称出现在 `ToolRegistry`，LLM 可通过 Function Calling 发现并调用，结果以 `ToolResult` 返回对话

#### Scenario: server 不可达降级
- **WHEN** 某 MCP server 启动失败或连接超时
- **THEN** 跳过该 server（记 warning），其余 server 与内置工具不受影响，Agent 正常启动

#### Scenario: 未安装 mcp SDK
- **WHEN** 环境未安装 `mcp` extra 而配置了 mcp_servers
- **THEN** 记 warning 并跳过全部 MCP 连接，CLI / Server 正常可用

### Requirement: CLI /mcp 命令
系统 SHALL 在 REPL 提供 `/mcp` 命令族：`/mcp`（或 `/mcp list`）列出 server 及状态与工具数、`/mcp tools <name>` 查看某 server 工具、`/mcp add`、`/mcp remove`、`/mcp enable|disable <name>`、`/mcp reconnect <name>`。add/remove/enable/disable 持久化到配置文件；`/tools` 输出区分内置工具与 MCP 工具。

#### Scenario: 安装并使用 stdio server
- **WHEN** 用户执行 `/mcp add filesystem npx -y @modelcontextprotocol/server-filesystem .` 并确认
- **THEN** 配置写入 `nexus.yaml`，连接后其工具立即可在对话中通过 `mcp__filesystem__*` 调用

#### Scenario: 卸载 server
- **WHEN** 用户执行 `/mcp remove filesystem`
- **THEN** 断开连接、从 `ToolRegistry` 注销其全部工具、从配置文件移除该节

### Requirement: Server MCP REST 契约
系统 SHALL 暴露 `/api/mcp` 契约：`GET /api/mcp`（server 列表：配置 + 运行状态 + 工具数）、`POST /api/mcp`（安装）、`PUT /api/mcp/{name}`（更新配置 / enable / disable）、`DELETE /api/mcp/{name}`（卸载）、`POST /api/mcp/{name}/reconnect`（重连刷新工具）。变更持久化配置并热生效（重建或增量更新 Agent 工具注册）。`GET /api/tools` SHALL 为每个工具标注 `origin`（builtin / mcp）与 `server` 字段。

#### Scenario: API 安装 server
- **WHEN** Desktop 调用 `POST /api/mcp` 提交合法 stdio server 配置
- **THEN** 配置持久化、建立连接、后续 `GET /api/tools` 返回该 server 的 `mcp__*` 工具且 `origin=mcp`

#### Scenario: 开关切换
- **WHEN** Desktop 调用 `PUT /api/mcp/{name}` 置 `enabled=false`
- **THEN** 断开该连接并注销其工具，配置持久化；`GET /api/mcp` 中该 server 状态为 disabled

### Requirement: Desktop MCP 管理
系统 SHALL 在 Desktop 提供 MCP 管理视图：server 卡片列表（名称 / 传输方式 / 连接状态 / 工具数）、安装表单（stdio 与 HTTP 两种模式）、启用开关、编辑、卸载、查看该 server 工具列表。ToolsView SHALL 按「内置工具 / MCP 工具」分组展示。所有操作仅通过 `/api/mcp` 与 `/api/tools` HTTP 契约完成。

#### Scenario: Desktop 端到端管理
- **WHEN** 用户在 MCP 视图安装一个 server、切换开关、查看工具、再卸载
- **THEN** 各操作经 REST 契约生效，视图状态与 `GET /api/mcp` 返回一致

## MODIFIED Requirements

### Requirement: 工具列表展示
CLI `/tools` 与 `GET /api/tools` 原来仅覆盖内置工具；现 SHALL 包含 MCP 工具并区分来源分组展示。

## REMOVED Requirements

无。
