# Tasks

- [x] Task 1: CLI 显示层优化（display.py / repl.py / main.py）
  - [x] SubTask 1.1: display.py —— 新增 Thinking 流式 gutter 渲染（`print_thinking_start` 打印带竖线的标题，`print_thinking_chunk` 按行加 dim `│ ` 前缀），新增 `render_tools_table(tools)` 与 `render_help_panel()`，紧凑化 `render_tool_call`
  - [x] SubTask 1.2: repl.py —— `_execute_task` 启用 `render_user_message` 回显用户输入；`/tools`、`/help` 改用新的表格/面板渲染
  - [x] SubTask 1.3: main.py —— `_run_single` 同步 Thinking gutter 渲染
  - [x] SubTask 1.4: 更新 `tests/test_cli_display.py` 覆盖新渲染方法，运行全部测试无回归

- [x] Task 2: Nexus Server 后端适配层（nexus/server/）
  - [x] SubTask 2.1: `nexus/server/app.py` —— FastAPI 应用工厂：加载配置、创建 Agent、挂载路由与静态资源；pyproject 增加 `server` extra（fastapi、uvicorn）与 `nexus ui`/`nexus serve` 命令派发
  - [x] SubTask 2.2: REST 路由 —— `GET/PUT /api/config`（读写 nexus.yaml，api_key 脱敏选项）、`GET /api/sessions`、`GET /api/sessions/{id}`、`DELETE /api/sessions/{id}`、`GET /api/tools`
  - [x] SubTask 2.3: WebSocket `/ws/chat` —— 多轮对话管理（会话历史按连接维护），订阅 EventBus 推送 `thinking_delta`/`content_delta`/`tool_call`/`usage`/`done`/`error` 事件
  - [x] SubTask 2.4: 新增 `tests/test_server_api.py`（REST 全量 + WebSocket 事件流，使用 mock LLM），运行全部测试无回归

- [x] Task 3: Nexus Desktop 前端（desktop/，React 18 + TS + Vite + Tailwind + Zustand + Framer Motion）
  - [x] SubTask 3.1: 工程脚手架 —— Vite+TS+Tailwind 初始化，深色数据艺术设计令牌（色板/字体/发光纹理），基础布局（侧边栏 + 主区）
  - [x] SubTask 3.2: 会话页 —— 消息流（用户气泡/AI Markdown 回复/Thinking 可折叠卡片/工具调用卡片），WebSocket 流式接收，输入区（多行、快捷键发送）
  - [x] SubTask 3.3: 会话管理 —— 历史会话列表、恢复、删除、新建会话
  - [x] SubTask 3.4: 配置页 —— Providers 编辑（api_key/model/max_tokens/context_window/base_url）、默认 Provider、Agent（system_prompt/max_steps）、工具开关、流式开关，保存写回
  - [x] SubTask 3.5: 工具页 —— 已注册工具列表与参数 schema 展示

- [x] Task 4: Electron 壳与联调
  - [x] SubTask 4.1: Electron 主进程 —— 启动时拉起（或附着已有）`nexus serve`，就绪探测后加载渲染页面，窗口/中文菜单
  - [x] SubTask 4.2: 构建联调 —— 前端 build、Electron 启动验证（SMOKE_OK）、`nexus ui` 浏览器模式验证

- [x] Task 5: 端到端测试与验收
  - [x] SubTask 5.1: 运行 pytest 全量（CLI + Server）—— 274 passed，仅 3 个 Windows 既有环境性失败
  - [x] SubTask 5.2: 前端 build 无错误，用浏览器自动化对 Web 模式做 UI 冒烟测试（会话发送、配置保存、会话列表）—— 7/7 通过
  - [x] SubTask 5.3: Electron 启动冒烟（窗口打开、后端连接、发消息）—— SMOKE_OK
- [x] Task 6: 修复桌面端会话持久化（验收中发现：WS 对话未落盘 SessionManager，已修复 done 事件携带 session_id、覆盖式保存，新增 2 项测试）

# Task Dependencies
- [Task 2] 与 [Task 1] 无依赖，可并行
- [Task 3] 依赖 [Task 2] 的 API 契约（可按契约先行，联调在 Task 4）
- [Task 4] 依赖 [Task 2] 与 [Task 3]
- [Task 5] 依赖 [Task 1]~[Task 4]
