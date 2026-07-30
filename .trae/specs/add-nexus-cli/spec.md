# Nexus CLI Agent 工具 Spec

## Why

当前 Nexus Agent Runtime 已提供 Python API（`Agent.run()`），但缺乏命令行入口。用户需要编写 Python 脚本才能使用 Agent 能力。参考 [opencode](https://github.com/sst/opencode) 和 [pi](https://github.com/sst/pi) 的 CLI 体验，提供一个可安装的 CLI 工具（`nexus` 命令），让用户直接在终端中使用 Agent 进行交互式编程助理，降低使用门槛，同时验证 Runtime 的完整性。

## What Changes

### 新增 CLI 工具

在项目中新增 CLI 入口，提供以下核心能力：

- **`nexus` 命令入口**：通过 `pip install` 后直接可用，或在开发环境通过 `python -m nexus.cli` 运行
- **交互式 REPL**：类 ChatGPT 的终端对话界面，支持多轮对话、对话历史
- **工具调用可视化**：在终端中以 Rich 组件展示 Agent 思考过程、工具调用、执行结果
- **流式输出**：利用 BaseLLM.stream_chat() 实现逐字流式输出，模拟真实 LLM 对话体验
- **配置管理**：支持命令行参数（`--model`、`--api-key`、`--system-prompt`）、环境变量、配置文件
- **文件系统工具**：内置文件读写工具（read_file、write_file、list_dir），使 CLI Agent 能操作项目文件
- **会话管理**：支持保存/加载对话历史、`--continue` 恢复上次对话

### 参考设计（opencode / pi 模式）

```
$ nexus "帮我分析 app.py 中的性能问题"

  🧠 Thinking...
  │ 分析 app.py 中的性能瓶颈...
  │
  ├─ 🔧 read_file("app.py")
  │  └─ ✅ 读取完成 (156 lines)
  │
  ├─ 💬 Response:
  │  发现以下性能问题：
  │  1. 第 42 行在循环中重复创建数据库连接
  │  ...
  │
  └─ ⏱️ 耗时 3.2s · Tokens: 1,234

$ nexus
💬 进入交互模式，输入 quit 退出
> 列出当前目录所有 Python 文件
  ...
> 给我写一个快速排序的函数
  ...
```

### 新增文件和模块

```
nexus/
├── cli/                          # CLI 工具模块（新增）
│   ├── __init__.py               # 模块入口
│   ├── main.py                   # CLI 主入口（argparse + 命令派发）
│   ├── repl.py                   # 交互式 REPL（rich + prompt_toolkit）
│   ├── display.py                # 终端输出美化（Rich 组件）
│   ├── config.py                 # 配置管理（参数/环境变量/配置文件）
│   ├── tools/                    # CLI 专用内置工具
│   │   ├── __init__.py
│   │   ├── file_tools.py         # 文件读写工具
│   │   ├── shell_tools.py        # Shell 命令执行工具（预留）
│   │   └── search_tools.py       # 代码搜索工具（预留）
│   └── session.py                # 会话管理（保存/加载/历史）
└── __main__.py                   # python -m nexus 入口（新增）
```

### 配置管理

支持五级配置优先级（从高到低）：
1. **命令行参数**：`--model gpt-4o --api-key sk-xxx --provider openai`
2. **环境变量**：`NEXUS_MODEL`、`NEXUS_API_KEY`、`NEXUS_BASE_URL`、`NEXUS_PROVIDER`、`NEXUS_MAX_STEPS`、`NEXUS_MAX_TOKENS`
3. **项目级配置文件**：`<work_dir>/nexus.yaml`（向后兼容 `.nexus.json`）
4. **用户级配置文件**：`~/.nexus/nexus.yaml`（向后兼容 `~/.nexus/config.json`）
5. **内置默认值**：openai/anthropic/minimax 三个 provider 的默认模型与参数

配置文件格式（YAML）：
```yaml
providers:
  openai:
    api_key: sk-xxx
    model: gpt-4o-mini
    max_tokens: 4096
    context_window_tokens: 128000
    base_url: null
default_provider: openai
agent:
  system_prompt: "You are a helpful coding assistant."
  max_steps: 30
tools:
  enabled:
    - read_file
    - write_file
    - list_dir
```

### 依赖新增

| 包 | 用途 |
|---|---|
| `rich>=13.0.0` | 终端富文本输出（Markdown 渲染、表格、面板、进度条） |
| `prompt-toolkit>=3.0.0` | 交互式输入（多行编辑、历史、自动补全） |

这些作为可选依赖 `[cli]` 加入 pyproject.toml，不影响核心 Runtime 的轻量性。

## Impact
- Affected specs: 无（新功能，不影响现有模块）
- Affected code: 新增 `nexus/cli/`、`nexus/__main__.py`；修改 `pyproject.toml`（新增可选依赖 + 入口点声明）

## ADDED Requirements

### Requirement: CLI 入口命令
The system SHALL 提供 `nexus` 命令入口，支持通过 `pip install` 后的 console_scripts 和 `python -m nexus` 两种方式启动。

#### Scenario: 命令行安装后可用
- **WHEN** 用户执行 `pip install -e ".[cli]"`
- **THEN** `nexus` 命令应在 PATH 中可用
- **AND** `python -m nexus` 等效

#### Scenario: 传递 prompt 直接执行
- **WHEN** 用户执行 `nexus "帮我写一个快速排序"`
- **THEN** 系统应直接执行任务并输出结果，不进入 REPL

#### Scenario: 无参数进入交互模式
- **WHEN** 用户执行 `nexus`（不带参数）
- **THEN** 系统应进入交互式 REPL 模式

### Requirement: 交互式 REPL
The system SHALL 提供基于 prompt_toolkit 的交互式 REPL，支持多轮对话、历史记录、多行输入。

#### Scenario: 多轮对话
- **WHEN** 用户在 REPL 中连续输入多条消息
- **THEN** 每次对话应保持上下文（messages 累积），Agent 应能引用之前的对话内容

#### Scenario: 退出命令
- **WHEN** 用户输入 `quit`、`exit` 或按 `Ctrl+D`
- **THEN** REPL 应退出并显示会话统计

#### Scenario: 多行输入
- **WHEN** 用户需要输入多行内容
- **THEN** 应支持大括号、三引号自动补全等 prompt_toolkit 多行编辑特性

### Requirement: 流式终端输出
The system SHALL 在终端中以流式方式展示 LLM 响应，利用 Rich 提供的 Markdown 渲染能力。

#### Scenario: 逐字流式输出
- **WHEN** Agent 调用 `llm.stream_chat()` 获取流式响应
- **THEN** 终端应每收到一个 LLMChunk 就刷新显示，实现逐字打印效果
- **AND** 不应等待完整响应后再一次性输出

#### Scenario: 工具调用可视化
- **WHEN** Agent 执行工具调用
- **THEN** 终端应以 Rich Panel/Tree 组件展示工具名称、参数、执行结果
- **AND** 工具执行期间显示 spinner 动画
- **AND** 工具执行完毕后显示结果摘要（成功/失败 + 耗时）

### Requirement: 配置管理
The system SHALL 支持命令行参数、环境变量、配置文件三级配置，优先级为参数 > 环境变量 > 配置文件。

#### Scenario: 命令行覆盖环境变量
- **WHEN** 用户设置了 `NEXUS_MODEL=gpt-4o-mini` 环境变量
- **AND** 同时传入 `--model gpt-4o`
- **THEN** 实际使用的模型应为 `gpt-4o`

#### Scenario: 配置文件自动加载
- **WHEN** 用户未提供命令行参数和环境变量
- **AND** `~/.nexus/config.json` 存在
- **THEN** 系统应自动加载配置文件的设置

#### Scenario: 项目级配置
- **WHEN** 当前目录存在 `.nexus.json`
- **THEN** 项目级配置应覆盖用户级配置，但优先级低于命令行参数和环境变量

### Requirement: CLI 内置文件系统工具
The system SHALL 内置文件系统操作工具（read_file、write_file、list_dir），使 CLI Agent 能读写项目文件。

#### Scenario: read_file 工具
- **WHEN** Agent 调用 read_file 工具传入文件路径
- **THEN** 应返回文件内容（含行号），支持指定行范围
- **AND** 自动限制返回行数（默认 500 行），防止上下文爆炸

#### Scenario: write_file 工具
- **WHEN** Agent 调用 write_file 工具传入文件路径和内容
- **THEN** 应写入文件，并在覆盖前输出警告确认（可通过参数跳过）

#### Scenario: list_dir 工具
- **WHEN** Agent 调用 list_dir 工具传入目录路径
- **THEN** 应返回目录内容列表（文件/子目录），支持递归深度控制

### Requirement: 对话历史与会话管理
The system SHALL 支持对话历史保存、加载和跨会话恢复。

#### Scenario: 保存会话
- **WHEN** 用户退出 REPL
- **THEN** 当前对话历史应自动保存到 `~/.nexus/sessions/` 目录（JSON 格式）
- **AND** 文件名包含时间戳和对话摘要

#### Scenario: 恢复会话
- **WHEN** 用户执行 `nexus --continue`
- **THEN** 应加载最近的会话历史，在加载的上下文中继续对话

#### Scenario: 列出历史会话
- **WHEN** 用户执行 `nexus --list-sessions`
- **THEN** 应列出所有已保存的会话（文件名 + 时间 + 首条消息摘要）

### Requirement: 日志与调试
The system SHALL 在 CLI 模式下将日志输出到文件和控制台，支持 `--verbose` / `--debug` 控制日志级别。

#### Scenario: verbose 模式
- **WHEN** 用户执行 `nexus --verbose`
- **THEN** 终端应显示 INFO 级别日志（如工具调用详情、token 统计）

#### Scenario: debug 模式
- **WHEN** 用户执行 `nexus --debug`
- **THEN** 终端应显示 DEBUG 级别日志（如完整消息内容、LLM 请求详情）

### Requirement: 注释与设计文档
The system SHALL 在 CLI 模块每个文件包含 docstring 说明职责和设计思路，关键流程有行内注释。

### Requirement: 测试
The system SHALL 提供 CLI 模块的单元测试（config 加载、display 组件、session 管理），使用 Mock LLM 避免依赖真实 API。
