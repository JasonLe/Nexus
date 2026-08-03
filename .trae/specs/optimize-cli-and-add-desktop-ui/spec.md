# CLI 界面优化 + Nexus Desktop（Electron 桌面端）Spec

## Why

当前 CLI 交互界面中用户输入、AI Thinking、AI 回复、工具调用堆叠在一起无法辨识（流式 append 模式下 Thinking 与回复无视觉边界），`/tools`、`/help` 输出为裸文本。同时项目缺少图形界面，配置（providers/模型/工具/系统提示词）只能手改 YAML，门槛高。需要一个与 Agent 核心解耦、覆盖全部 CLI 能力的桌面端。

## What Changes

### Part A：CLI 显示与交互优化
- 用户输入提交后回显为绿色 `👤 You` Panel，不再消失在 prompt 行中
- Thinking 流式输出加 dim 左侧竖线 gutter（`│ `），形成连续可视块，与正文明确区分（保持纯 append、滚动安全）
- AI 回复保留 `🤖 Nexus` 头部 + Markdown 段落渲染，轮次间分隔线强化
- `/tools` 改为 Rich Table（名称/描述/参数摘要），`/help` 改为 Rich Panel 排版
- 工具调用 Panel 紧凑化（单行摘要优先，失败红色高亮）
- 上述改动同步覆盖 `_run_single` 单次执行模式

### Part B：Nexus Server（后端适配层，新增 `nexus/server/`）
- 基于 FastAPI 的本地服务，作为 Desktop 与 Agent 核心之间的**唯一桥梁**（Desktop 不 import agent 内部逻辑，只走 HTTP/WebSocket）
- REST：配置的读取/保存（复用 `nexus.cli.config.load_config/save_config`）、会话列表/读取/删除（复用 `SessionManager`）、已注册工具列表
- WebSocket `/ws/chat`：多轮对话，实时推送 thinking delta / content delta / tool_call / usage / done / error 事件（订阅现有 EventBus）
- 新增 CLI 命令 `nexus ui`（启动服务并打开浏览器）与 `nexus serve`（仅启动服务，供 Electron 附着）

### Part C：Nexus Desktop（新增 `desktop/`，Electron + React 18 + TS + Vite + Tailwind + Zustand + Framer Motion）
- 深色数据艺术风、全中文界面
- 会话页：流式 Thinking（可折叠卡片）、Markdown 回复、工具调用卡片、多轮对话、会话列表/恢复/删除/新建
- 配置页：Providers（api_key/model/max_tokens/context_window/base_url）、默认 Provider、Agent（system_prompt/max_steps）、工具开关、流式开关 —— 全部可视化编辑并写回 nexus.yaml
- 工具页：查看已注册工具及参数 schema
- Electron 主进程负责拉起/附着 Python 后端（`nexus serve`），渲染进程只访问 HTTP/WebSocket
- **解耦约束**：`desktop/` 为独立目录，不修改 `nexus/core` 任何决策逻辑；如需配合仅通过新增 server 适配层

## Impact

- Affected specs: `add-nexus-cli`（显示层增强，不改变命令语义）
- Affected code:
  - 修改：`nexus/cli/display.py`、`nexus/cli/repl.py`、`nexus/cli/main.py`、`tests/test_cli_display.py`、`pyproject.toml`（新增 server 依赖与 `nexus ui/serve` 入口说明）
  - 新增：`nexus/server/`（app/routes/ws 适配层）、`desktop/`（Electron+React 工程）、`tests/test_server_api.py`

## ADDED Requirements

### Requirement: CLI 角色化视觉分层
系统 SHALL 在 REPL 与单次执行模式中，让用户输入、Thinking、AI 回复、工具调用四者具备稳定可辨识的视觉样式：用户消息为绿色边框 Panel；Thinking 流式块带 dim 左侧竖线 gutter 且内容 dim italic；AI 回复前有 `🤖 Nexus` 头部；工具调用为紧凑 Panel（成功青色/失败红色）。

#### Scenario: 多轮对话可辨识
- **WHEN** 用户在 REPL 中进行多轮含工具调用的对话
- **THEN** 每轮用户输入以绿色 Panel 固定在历史流中，Thinking 块有连续竖线边界，回复为亮色 Markdown，轮次间有分隔线

#### Scenario: /tools 与 /help 美化
- **WHEN** 用户输入 `/tools` 或 `/help`
- **THEN** 分别以 Rich Table（工具名/描述/参数）与 Rich Panel（命令列表+快捷键）渲染，不再输出裸 print 文本

### Requirement: 本地 Agent 服务
系统 SHALL 提供 `nexus serve`（启动 HTTP+WebSocket 服务）与 `nexus ui`（启动并打开浏览器）命令；服务暴露配置读写、会话管理、工具查询 REST API 与 `/ws/chat` 流式对话 WebSocket，事件语义与 CLI 事件流一致。

#### Scenario: 配置读写
- **WHEN** 客户端 PUT 一份合法配置到 `/api/config`
- **THEN** 服务将其写入用户级 `~/.nexus/nexus.yaml`（权限 0600）并返回生效配置

#### Scenario: 流式对话
- **WHEN** 客户端通过 `/ws/chat` 发送用户消息
- **THEN** 服务依次推送 `thinking_delta`/`content_delta`/`tool_call` 事件，结束时推送含 token 用量的 `done` 事件，异常时推送 `error`

### Requirement: Nexus Desktop 桌面应用
系统 SHALL 提供 `desktop/` 独立 Electron 工程：主进程可拉起或附着 Python 后端；渲染进程为深色数据艺术风中文界面，覆盖 CLI 全部能力（对话含流式 Thinking/工具卡片、会话管理、配置管理、工具查看）。

#### Scenario: 配置可视化
- **WHEN** 用户在配置页修改某 provider 的 model 与 api_key 并保存
- **THEN** 配置写回 nexus.yaml，新会话立即生效

#### Scenario: 解耦
- **WHEN** 检查 `desktop/` 代码
- **THEN** 其仅通过 HTTP/WebSocket 与后端交互，不包含对 `nexus/core` 决策逻辑的复制或修改

## MODIFIED Requirements

### Requirement: CLI 显示层（add-nexus-cli）
`DisplayManager` 增加/调整：Thinking 流式 gutter 渲染、`render_user_message` 在 REPL 中启用、`/tools`、`/help` 的表格化渲染；保持既有公开方法签名向后兼容（新增参数带默认值）。
