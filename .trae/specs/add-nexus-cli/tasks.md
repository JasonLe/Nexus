# Tasks

## 阶段一：基础设施（串行）

- [x] Task 1: 更新 pyproject.toml 与项目配置
  - [x] SubTask 1.1: 在 pyproject.toml 新增 `[project.scripts]` 入口点 `nexus = "nexus.cli.main:main"`
  - [x] SubTask 1.2: 新增 `[project.optional-dependencies]` 的 `cli` 组：`rich>=13.0.0`, `prompt-toolkit>=3.0.0`
  - [x] SubTask 1.3: 创建 `nexus/__main__.py`（`python -m nexus` 入口，委托给 cli.main:main）

- [x] Task 2: 实现配置管理系统
  - [x] SubTask 2.1: 创建 `nexus/cli/config.py`：定义 `CLIConfig` dataclass（model/provider/api_key/base_url/system_prompt/max_steps/work_dir）、三级加载函数 `load_config()`（参数 dict > 环境变量 > ~/.nexus/config.json > .nexus.json）、默认值
  - [x] SubTask 2.2: 实现配置文件读写：`ConfigLoader` 类支持 JSON 格式，自动创建 `~/.nexus/` 目录

## 阶段二：核心功能（可并行）

- [x] Task 3: 实现终端输出美化模块
  - [x] SubTask 3.1: 创建 `nexus/cli/display.py`：`DisplayManager` 类，基于 Rich 实现：
    - Markdown 渲染（`rich.markdown.Markdown`）
    - 流式内容追加 `render_response(content_stream)`
    - 工具调用面板 `render_tool_call(tool_name, args, result)` 含 spinner
    - 错误面板 `render_error(error_message)`
    - 统计摘要 `render_summary(steps, tokens, duration)`
    - 思考过程 `render_thinking(text)` 含 spinner
  - [x] SubTask 3.2: 适配 Rich Console 的 live update 能力，使流式输出不会闪烁

- [x] Task 4: 实现 CLI 内置文件系统工具
  - [x] SubTask 4.1: 创建 `nexus/cli/tools/file_tools.py`：实现 `ReadFileTool`（支持行范围，限制 500 行）、`WriteFileTool`（覆盖前输出 diff/确认）、`ListDirTool`（支持递归深度）、`SearchContentTool`（grep 搜索文件内容）
  - [x] SubTask 4.2: 工具需注册到 ToolRegistry 并暴露给 Agent

- [x] Task 5: 实现会话管理
  - [x] SubTask 5.1: 创建 `nexus/cli/session.py`：`SessionManager` 类：
    - `save(state: AgentState, metadata: dict)` 保存到 `~/.nexus/sessions/`（JSON，含 messages/steps/元数据）
    - `load(session_id: str)` 加载指定会话
    - `list_sessions()` 列出历史会话
    - `delete(session_id: str)` 删除会话
    - 自动截断过长历史（保留最近 N 轮），避免上下文膨胀

## 阶段三：CLI 主流程（依赖阶段一、二）

- [x] Task 6: 实现 CLI 主入口
  - [x] SubTask 6.1: 创建 `nexus/cli/main.py`：`main()` 函数和 argparse 参数解析：
    - 直接执行模式：`nexus "prompt"` → 创建 Agent → 执行 → 输出结果 → 退出
    - 交互模式：`nexus`（无参数）→ 进入 REPL
    - 参数：`--model`, `--api-key`, `--base-url`, `--system-prompt`, `--max-steps`, `--work-dir`, `--continue`, `--list-sessions`, `--verbose`, `--debug`, `--config`
  - [x] SubTask 6.2: 实现 StreamHandler（BaseLLM.stream_chat 的事件处理），将 LLMChunk 转发给 DisplayManager

- [x] Task 7: 实现交互式 REPL
  - [x] SubTask 7.1: 创建 `nexus/cli/repl.py`：`Repl` 类：
    - 基于 prompt_toolkit 的 `PromptSession`，支持多行输入（空行提交 vs 显式提交）
    - 会话级 Agent 实例（进入 REPL 时创建，退出时销毁）
    - 快捷键：Ctrl+D 退出，Ctrl+C 中断当前任务
    - 每次用户输入后：创建新 task → Agent.run() → 流式展示 → 等待下一次输入
    - 内置命令：`/clear`（清空上下文）、`/save`（手动保存会话）、`/tools`（列出可用工具）、`/quit`
    - 历史持久化（prompt_toolkit 的 FileHistory）
  - [x] SubTask 7.2: REPL 中集成流式输出：利用 DisplayManager.render_response() 展示 LLM 实时流式响应

## 阶段四：测试与文档

- [x] Task 8: 编写单元测试
  - [x] SubTask 8.1: `tests/test_cli_config.py` 测试 ConfigLoader 三级加载逻辑
  - [x] SubTask 8.2: `tests/test_cli_display.py` 测试 DisplayManager 组件输出格式
  - [x] SubTask 8.3: `tests/test_cli_tools.py` 测试 FileTool read/write/list 功能
  - [x] SubTask 8.4: `tests/test_cli_session.py` 测试 SessionManager 保存/加载/列表

- [x] Task 9: 更新 README
  - [x] SubTask 9.1: 在 README 新增「CLI 工具」章节：安装（`pip install -e ".[cli]"`）、快速开始、命令参考、配置说明

# Task Dependencies

- Task 1（pyproject.toml）是所有后续 Task 的前置
- Task 3、4、5 可并行（互相独立）
- Task 6 依赖 Task 2 + Task 3
- Task 7 依赖 Task 3 + Task 4 + Task 5 + Task 6
- Task 8 依赖 Task 2/3/4/5
- Task 9 依赖全部完成
