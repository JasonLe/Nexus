# Tasks

- [x] Task 1: 创建 ShellTool 工具类
  - [x] SubTask 1.1: 创建 `nexus/cli/tools/shell_tool.py`，实现 `ShellTool(BaseTool)`
  - [x] SubTask 1.2: 实现操作系统检测逻辑（`sys.platform` 判断 Windows vs Unix）
  - [x] SubTask 1.3: 实现 `execute()` 方法：使用 `asyncio.create_subprocess_exec` 执行命令，区分 cmd.exe 和 bash
  - [x] SubTask 1.4: 实现超时控制（`asyncio.wait_for`，默认 30 秒，超时杀进程）
  - [x] SubTask 1.5: 实现输出截断（stdout/stderr 各 10000 字符上限）
  - [x] SubTask 1.6: 实现 schema 定义（command 必填、timeout/work_dir 可选）

- [x] Task 2: 注册 ShellTool 到 CLI
  - [x] SubTask 2.1: 在 `nexus/cli/tools/__init__.py` 导出 ShellTool
  - [x] SubTask 2.2: 在 `nexus/cli/main.py` 的 `_register_tools` 中添加 ShellTool，注入 work_dir

- [x] Task 3: 编写测试
  - [x] SubTask 3.1: 创建 `tests/test_shell_tool.py`
  - [x] SubTask 3.2: 测试简单命令执行（echo）
  - [x] SubTask 3.3: 测试非零退出码场景
  - [x] SubTask 3.4: 测试超时场景
  - [x] SubTask 3.35: 测试输出截断
  - [x] SubTask 3.6: 测试工作目录设置
  - [x] SubTask 3.7: 测试参数校验（缺少 command）

# Task Dependencies
- Task 2 依赖 Task 1（需要 ShellTool 类先存在）
- Task 3 依赖 Task 1（测试需要 ShellTool 实现）
