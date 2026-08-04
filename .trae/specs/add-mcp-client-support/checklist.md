# Checklist

- [x] `nexus/tools/mcp/` 子包存在：client.py（stdio + HTTP 传输）、adapter.py（schema 透传 + ToolResult 包装 + `mcp__<server>__<tool>` 命名）、manager.py（连接状态管理 + 单 server 失败降级）、plugin.py（MCPPlugin 生命周期）
- [x] `pyproject.toml` 包含 `mcp` 可选 extra；未安装 mcp SDK 时配置了 mcp_servers 仅记 warning 不报错
- [x] `nexus.yaml` 支持 `mcp_servers` 节（command/args/env/url/enabled），`load_config()` 正确解析且非法配置容错跳过
- [x] `save_config()` 与 `generate_config_template()` 覆盖 `mcp_servers`
- [x] `create_agent()` 在有已启用 MCP server 时安装 MCPPlugin（经 `create_agent_async`/`install_mcp` 与 Server startup 钩子）；Runtime 代码零修改
- [x] MCP 工具经 Function Calling 可被 LLM 发现并调用，结果以 ToolResult 返回（adapter 包装 + 端到端实测）
- [x] CLI `/mcp` 命令族可用：list / tools / add / remove / enable / disable / reconnect；add/remove/enable/disable 持久化到配置文件
- [x] CLI `/tools` 输出区分内置工具与 MCP 工具
- [x] Server 暴露 `/api/mcp` 五个端点且变更持久化并热生效；`GET /api/tools` 返回 `origin` / `server` 字段
- [x] Desktop 提供 MCP 管理视图：安装（stdio/HTTP 表单）、开关、卸载、查看工具、状态展示
- [x] Desktop ToolsView 按「内置工具 / MCP 工具」分组
- [x] Desktop 所有 MCP 操作仅通过 HTTP REST 契约完成，无直接后端耦合
- [x] pytest 新增 MCP 用例全部通过（36 个单元用例 + 7 个 API 契约用例），既有测试无回归（336 passed，仅 3 个既有 Windows shell 基线失败）
- [x] 端到端验证：官方 filesystem MCP server 真实连接跑通（Server API 端到端：14 个 MCP 工具注册、origin/server 标注正确；Desktop 前端 tsc + vite build 通过；CLI 核心层 list_tools/call_tool 与插件生命周期实测通过）
