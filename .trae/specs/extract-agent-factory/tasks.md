# Tasks

- [x] Task 1: 新建 `nexus/core/factory.py` 工厂模块
  - [x] SubTask 1.1: 实现 `create_llm(config) -> BaseLLM`（从 cli/main.py 的 `_create_llm` 迁移）
  - [x] SubTask 1.2: 实现 `register_tools(agent, config) -> None`（从 cli/main.py 的 `_register_tools` 迁移）
  - [x] SubTask 1.3: 实现 `create_agent(config) -> Agent` 单一入口（组合 create_llm + Agent 构造 + register_tools）
  - [x] SubTask 1.4: 单元测试 `tests/test_factory.py` 覆盖三个 provider 分支 + 工具过滤

- [x] Task 2: 改造 `nexus/cli/main.py` 调用 factory
  - [x] SubTask 2.1: `main()` 中 Agent 创建改为 `create_agent(config)`
  - [x] SubTask 2.2: `_sessions_restore` 中 Agent 创建改为 `create_agent(config)`
  - [x] SubTask 2.3: 保留 `main.py` 内的 `_create_llm` / `_register_tools` 薄包装（调用 factory），或直接删除并更新所有 import

- [x] Task 3: 改造 `nexus/server/app.py` 调用 factory
  - [x] SubTask 3.1: `create_app()` 中 Agent 创建改为 `create_agent(config)`，删除从 `cli.main` 的 import
  - [x] SubTask 3.2: `PUT /api/config` 的重建逻辑改为 `create_agent(cfg)`，替换 `app.state.agent`

- [x] Task 4: 改造 `nexus/cli/repl.py` 调用 factory
  - [x] SubTask 4.1: `/clear` 命令的 Agent 重建改为 `create_agent(config)`，删除从 `cli.main` 的 import

- [x] Task 5: 验证与回归
  - [x] SubTask 5.1: 运行全量测试 `pytest tests/`，确保全部通过
  - [ ] SubTask 5.2: 手动验证 PUT /api/config 切换 provider 后新对话使用新 LLM
  - [ ] SubTask 5.3: 手动验证 CLI `nexus` REPL /clear 命令仍正常工作

# Task Dependencies

- [Task 2] [Task 3] [Task 4] 都依赖 [Task 1]
- [Task 5] 依赖 [Task 2] [Task 3] [Task 4] 全部完成
