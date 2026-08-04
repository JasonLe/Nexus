# Tasks

- [x] Task 1: MCP 核心层 —— 新增 `nexus/tools/mcp/` 子包
  - [x] SubTask 1.1: `pyproject.toml` 新增 `mcp` 可选 extra（官方 mcp Python SDK），未安装时优雅降级
  - [x] SubTask 1.2: `client.py` —— `MCPClient` 封装 stdio 与 Streamable HTTP 两种传输的连接、list_tools、call_tool、close
  - [x] SubTask 1.3: `adapter.py` —— `MCPToolAdapter`（BaseTool 子类）：schema 透传、call_tool 结果包装为 ToolResult、工具名 `mcp__<server>__<tool>` 前缀
  - [x] SubTask 1.4: `manager.py` —— `MCPManager`：按配置批量连接/断开 server、维护 server 状态（connected/error/disabled）、工具注册与注销、单 server 重连；单 server 失败不影响其余
- [x] Task 2: 配置层 —— `mcp_servers` 接入五级配置体系
  - [x] SubTask 2.1: `nexus/cli/config.py` 新增 `MCPServerConfig` 数据类与 `NexusConfig.mcp_servers` 字段
  - [x] SubTask 2.2: `_apply_file_config()` 解析 `mcp_servers` 节（含 `${ENV_VAR}` 引用、非法配置容错跳过）
  - [x] SubTask 2.3: `save_config()` 与 `generate_config_template()` 同步覆盖 `mcp_servers`
- [x] Task 3: 插件接入 —— `MCPPlugin` + factory 挂载
  - [x] SubTask 3.1: `nexus/tools/mcp/plugin.py` —— `MCPPlugin`：install 阶段创建 MCPManager 并注册适配工具，deactivate 断开全部连接；Runtime 零修改
  - [x] SubTask 3.2: `nexus/core/factory.py` —— `create_agent()` 在存在已启用 mcp_servers 时安装 MCPPlugin
- [x] Task 4: CLI —— `/mcp` 命令族
  - [x] SubTask 4.1: `repl.py` 新增 `/mcp` 命令路由（list / tools / add / remove / enable / disable / reconnect），补全器加入 `/mcp`
  - [x] SubTask 4.2: `display.py` 新增 MCP server 状态表格渲染；`/tools` 输出区分内置工具与 MCP 工具分组
  - [x] SubTask 4.3: add / remove / enable / disable 写回配置文件并热生效（重连对应 server）
- [x] Task 5: Server —— `/api/mcp` REST 契约
  - [x] SubTask 5.1: `GET /api/mcp`（列表：配置 + 状态 + 工具数）、`POST /api/mcp`（安装）、`PUT /api/mcp/{name}`（更新/开关）、`DELETE /api/mcp/{name}`（卸载）、`POST /api/mcp/{name}/reconnect`；变更持久化并热生效
  - [x] SubTask 5.2: `GET /api/tools` 返回 `origin`（builtin/mcp）与 `server` 字段，包含已连接 MCP 工具
  - [x] SubTask 5.3: `_config_to_json()` / `_merge_config_json()` 覆盖 mcp_servers，配置变更后 agent 重建时携带 MCPPlugin
- [x] Task 6: Desktop —— MCP 管理视图 + 工具分组
  - [x] SubTask 6.1: `api/types.ts` + `api/client.ts` 新增 McpServer DTO 与 `/mcp` 系列 API 方法；`ToolInfo` 增加 `origin` / `server`
  - [x] SubTask 6.2: 新增 `views/McpView.tsx`：server 卡片（名称/传输/状态/工具数）、安装编辑表单（stdio/HTTP）、开关、卸载、查看工具；SideNav 与 App 接入新视图
  - [x] SubTask 6.3: `ToolsView.tsx` 按「内置工具 / MCP 工具」分组展示；`appStore` 增加 MCP 状态与操作
- [x] Task 7: 测试与验证
  - [x] SubTask 7.1: `tests/test_mcp_adapter.py`（schema 透传、结果包装、命名前缀）、`tests/test_mcp_config.py`（解析/保存/容错）、`tests/test_mcp_manager.py`（mock client 的连接/断开/降级）
  - [x] SubTask 7.2: `tests/test_server_api.py` 增加 `/api/mcp` 契约用例
  - [x] SubTask 7.3: 端到端验证：接入官方 filesystem MCP server，CLI `/mcp` 与 Desktop 冒烟通过

# Task Dependencies
- [Task 3] depends on [Task 1, Task 2]
- [Task 4] depends on [Task 3]
- [Task 5] depends on [Task 3]
- [Task 6] depends on [Task 5]
- [Task 7] depends on [Task 1-6]
- 并行说明：Task 1 与 Task 2 可并行；Task 4 与 Task 5 可并行
