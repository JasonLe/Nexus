# Shell Command Execution Tool Spec

## Why

当前 Nexus Agent 只有文件操作类工具（read/write/list/search），无法执行 shell 命令。LLM 在需要查看进程、检查环境变量、运行脚本、调用系统命令（如 git、npm、docker）时无能为力。需要一个跨平台的 shell 执行工具，让 Agent 能在受控边界内执行系统命令。

## What Changes

- 新增 `ShellTool` 工具类（继承 `BaseTool`），支持执行 shell 命令
- 自动检测操作系统（Windows / macOS / Linux），选择对应的 shell：
  - Windows → `cmd.exe /c`（或 PowerShell，MVP 用 cmd）
  - macOS/Linux → `/bin/bash -c`
- 支持超时控制（默认 30 秒，可配置）
- 支持工作目录设置（默认当前目录）
- 输出截断保护（stdout/stderr 各最多 10000 字符，超出截断并提示）
- 命令结果包含：退出码、stdout、stderr、执行耗时
- 在 `_register_tools` 中注册，可通过配置文件 `tools.enabled` 启用/禁用
- 安全边界：MVP 阶段不实现命令黑名单，但记录警告日志，由用户自行控制

## Impact

- Affected specs: add-nexus-cli（CLI 工具集扩展）
- Affected code:
  - `nexus/cli/tools/shell_tool.py`（新增）
  - `nexus/cli/tools/__init__.py`（导出 ShellTool）
  - `nexus/cli/main.py`（_register_tools 注册 ShellTool）
  - `tests/test_shell_tool.py`（新增测试）

## ADDED Requirements

### Requirement: Shell Command Execution

系统 SHALL 提供一个名为 `shell` 的工具，允许 Agent 在受控边界内执行 shell 命令，并自动适配 Windows 和 Unix（macOS/Linux）平台。

#### Scenario: 成功执行简单命令

- **WHEN** Agent 调用 `shell` 工具，参数 `command="echo hello"`
- **THEN** 工具返回 `ToolResult(success=True, data=...)`，data 包含 stdout="hello\n"、stderr=""、exit_code=0

#### Scenario: 区分操作系统

- **WHEN** 工具运行在 Windows 上时，命令通过 `cmd.exe /c` 执行
- **AND** 工具运行在 macOS/Linux 上时，命令通过 `/bin/bash -c` 执行
- **THEN** 用户无需指定 shell 类型，工具自动选择

#### Scenario: 命令执行失败（非零退出码）

- **WHEN** Agent 执行的命令返回非零退出码（如 `exit 1`）
- **THEN** 工具返回 `ToolResult(success=True, data=...)`（success 仍为 True，因为命令本身执行了，只是退出码非零），data 中 `exit_code` 字段反映真实退出码，stderr 字段包含错误输出

#### Scenario: 命令执行超时

- **WHEN** 命令执行时间超过 `timeout` 参数（默认 30 秒）
- **THEN** 工具终止进程，返回 `ToolResult(success=False, error="Command timed out after 30s")`

#### Scenario: 工作目录设置

- **WHEN** Agent 调用 `shell` 工具时传入 `work_dir="/tmp"`
- **THEN** 命令在该工作目录下执行
- **AND** 不传 `work_dir` 时使用工具构造时设置的默认工作目录

#### Scenario: 输出截断保护

- **WHEN** 命令 stdout 或 stderr 超过 10000 字符
- **THEN** 输出被截断为前 10000 字符，并追加 `\n... [output truncated]` 提示

#### Scenario: 参数校验

- **WHEN** Agent 调用 `shell` 工具时未提供 `command` 参数
- **THEN** ToolExecutor 的参数校验层返回 `ToolResult(success=False, error="Missing required parameter: command")`

#### Scenario: 通过配置文件启用/禁用

- **WHEN** `nexus.yaml` 配置 `tools.enabled: ["shell", "read_file"]`
- **THEN** 仅注册 shell 和 read_file 两个工具
- **AND** `tools.enabled` 为空或未设置时，shell 工具默认注册

## MODIFIED Requirements

### Requirement: CLI 工具注册

`nexus/cli/main.py` 的 `_register_tools` 函数 SHALL 在工具字典中包含 `ShellTool`，使其可通过配置文件控制启用。

## REMOVED Requirements

无。
