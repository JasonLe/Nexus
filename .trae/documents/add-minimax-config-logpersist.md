# Plan: 配置文件写入、日志持久化、MiniMax Provider

## Summary

三个相互独立但同属基础设施增强的功能：

1. **配置文件写入** — 支持创建/覆写用户级 `~/.nexus/config.json`（当前只能读取，无法通过 CLI 写入）
2. **日志持久化** — 在 `setup_logging()` 中增加 RotatingFileHandler，将日志写入 `~/.nexus/logs/nexus.log`
3. **MiniMax Provider** — 新增 `MiniMaxAnthropicLLM`，基于 Anthropic SDK 对接 `https://api.minimaxi.com/anthropic`

## Current State Analysis

### 配置文件系统现状
- `CLIConfig` dataclass（`nexus/cli/config.py`）已有完整字段定义
- `load_config()` 已实现三级加载（命令行 > 环境变量 > JSON 文件 > 默认值）
- **缺失**：没有 `save_config()` / `--save-config` 能力 — 用户无法持久化当前配置

### 日志现状
- `setup_logging()`（`nexus/cli/main.py:31`）只配置了 `StreamHandler`（输出到终端）
- 无文件 Handler — 日志在进程退出后丢失
- 日志持久化对排查 CLI Agent 的长时间运行问题至关重要

### Provider 现状
- `nexus/cli/main.py:287-292` 硬编码 `OpenAILLM(...)`，未根据 `config.provider` 动态选择
- `nexus/llm/providers/__init__.py` 为空文件
- 无 Anthropic SDK 依赖，无现有 Anthropic 相关代码

### 关键文件
| 文件 | 当前角色 | 需改动程度 |
|------|---------|-----------|
| `nexus/cli/config.py` | 配置加载 | 新增 `save_config()` |
| `nexus/cli/main.py` | CLI 入口 + `setup_logging()` + provider 创建 | 主要改动 |
| `nexus/llm/providers/__init__.py` | 空文件 | 新增导出 |
| `nexus/llm/providers/openai.py` | 参考模板 | 不变 |
| `pyproject.toml` | 项目依赖 | 新增 `anthropic` 依赖 |

---

## Proposed Changes

### 1. 配置文件写入 (`nexus/cli/config.py` + `nexus/cli/main.py`)

**What**: 新增 `save_config()` 函数和 `--save-config` CLI 参数。

**Why**: 用户可在非交互模式（`nexus "prompt" --model gpt-4o --save-config`）一键写入配置文件，下次无需重新指定参数。

**How**:

#### a. `nexus/cli/config.py` 新增函数

```python
def save_config(config: CLIConfig, path: str | None = None) -> str:
    """将当前 CLIConfig 保存为 JSON 配置文件。
    
    Args:
        config: 当前生效的 CLIConfig 实例
        path: 目标文件路径，None 时默认 ~/.nexus/config.json
    
    Returns:
        写入的文件路径
    """
```

- 自动创建父目录（`~/.nexus/`）
- 只写入非默认值字段（减少文件噪音）
- 已存在的 `api_key` 字段跳过（不在配置文件中明文存储密钥）

#### b. `nexus/cli/main.py` 改动

- `--save-config` 参数：在 `_run_single()` 结束后调用 `save_config()`
- `CLIConfig` 新增 `log_dir` / `log_file` 字段（默认 `~/.nexus/logs/nexus.log`）

### 2. 日志持久化 (`nexus/cli/main.py`)

**What**: `setup_logging()` 增加 `RotatingFileHandler`，日志写入 `~/.nexus/logs/nexus.log`。

**Why**: 长时间运行的 Agent 会话中，终端日志在进程退出后丢失。持久化日志对排查问题、审计 Agent 行为必不可少。

**How**:

#### `setup_logging()` 改动

```python
def setup_logging(verbose=False, debug=False, log_file=None):
    # 1. 终端 StreamHandler（保持现有行为）
    # 2. 新增 RotatingFileHandler -> ~/.nexus/logs/nexus.log
    #    - maxBytes=10MB, backupCount=5
    #    - level=DEBUG（文件始终记录完整日志）
    #    - 自动创建 ~/.nexus/logs/ 目录
```

关键设计：
- 文件日志**始终输出**（不论 verbose/debug），级别为 DEBUG，确保排查时有足够信息
- 终端日志级别由 verbose/debug 控制（保持现有行为不变）
- 使用 `RotatingFileHandler`（标准库内置，零额外依赖），防止日志文件无限增长
- 日志文件路径：`~/.nexus/logs/nexus.log`，加时间戳命名：`nexus-2026-07-29.log`

#### `CLIConfig` 新增字段

```python
log_dir: str = ""          # 日志目录，空字符串表示使用默认值 ~/.nexus/logs/
log_level: str = "WARNING" # 终端日志级别
```

### 3. MiniMax Provider (`nexus/llm/providers/minimax.py`)

**What**: 新增 `MiniMaxAnthropicLLM` 类，通过 Anthropic SDK 对接 MiniMax 的 Anthropic 兼容 API。

**Why**: MiniMax 提供了 Anthropic 兼容的 API 端点（`https://api.minimaxi.com/anthropic`），可以直接使用 `anthropic` Python SDK 对接，无需从零实现 HTTP 层。

**How**:

#### a. `pyproject.toml` 新增依赖

```toml
dependencies = [
    "openai>=1.0.0",
    "httpx>=0.27.0",
    "anthropic>=0.39.0",
    "pydantic>=2.0.0",
]
```

#### b. `nexus/llm/providers/minimax.py` 核心实现

```python
"""MiniMax Anthropic Provider —— 基于 Anthropic SDK 对接 MiniMax 的 Anthropic 兼容 API。

设计思路
--------
MiniMax 提供 Anthropic Messages API 兼容端点:
https://api.minimaxi.com/anthropic

直接使用 anthropic Python SDK，只需修改 base_url 和 api_key。
SDK 负责 HTTP 通信、流式处理、错误重试，Provider 层仅负责:
1. 连接管理（自定义 base_url + api_key）
2. 格式映射（Anthropic SDK 响应 -> Nexus LLMResponse / LLMChunk）
3. Tool calling 转换（Anthropic tool_use block <-> Nexus ToolCall）
4. 错误处理（Anthropic 异常 -> LLMError）

支持模型: MiniMax-Text-01, abab6.5s-chat 等
"""
```

**类名**: `MiniMaxAnthropicLLM(BaseLLM)`

**构造函数**:
```python
def __init__(self, api_key=None, base_url="https://api.minimaxi.com/anthropic",
             model="MiniMax-Text-01", **kwargs):
```

**chat() 映射**:
- Anthropic SDK `client.messages.create(messages=..., model=..., tools=...)` 
- 将 Anthropic `Message` 响应转换:
  - `content` blocks 中 `text` 块 -> `LLMResponse.content`
  - `tool_use` blocks -> `LLMResponse.tool_calls`（id/name/input）
  - `usage.input_tokens/output_tokens` -> `UsageStats`
  - `stop_reason` -> `finish_reason`（"end_turn"->"stop", "tool_use"->"tool_calls"）

**stream_chat() 映射**:
- 遍历 `client.messages.create(stream=True)` 事件
- `content_block_start/delta/stop` -> 累加 text/tool_use 并 yield LLMChunk

**关键转换逻辑**:
```
Anthropic content block types:
  text block     -> LLMResponse.content
  tool_use block -> ToolCall(id=block.id, name=block.name, arguments=block.input)

Anthropic stop_reason:
  "end_turn"   -> "stop"
  "tool_use"   -> "tool_calls"  
  "max_tokens" -> "length"
```

#### c. `nexus/cli/main.py` — Provider 工厂

将硬编码的 `OpenAILLM(...)` 替换为基于 `config.provider` 的动态选择：

```python
def _create_llm(config: CLIConfig) -> BaseLLM:
    """根据 config.provider 创建对应的 LLM 实例。"""
    if config.provider == "minimax":
        return MiniMaxAnthropicLLM(
            api_key=config.api_key,
            model=config.model or "MiniMax-Text-01",
            **({"base_url": config.base_url} if config.base_url else {}),
        )
    else:  # 默认 openai
        return OpenAILLM(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model or "gpt-4o-mini",
        )
```

`main()` 中原来 287-292 行替换为 `llm = _create_llm(config)`。

#### d. `nexus/llm/providers/__init__.py`

```python
from nexus.llm.providers.openai import OpenAILLM
from nexus.llm.providers.minimax import MiniMaxAnthropicLLM

__all__ = ["OpenAILLM", "MiniMaxAnthropicLLM"]
```

---

## Files Changed

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `pyproject.toml` | 修改 | 新增 `anthropic>=0.39.0` 为核心依赖 |
| `nexus/cli/config.py` | 修改 | 新增 `save_config()`、`log_dir`/`log_level` 字段 |
| `nexus/cli/main.py` | 修改 | `setup_logging()` 加 RotatingFileHandler；新增 `_create_llm()` 工厂；新增 `--save-config` 参数 |
| `nexus/llm/providers/minimax.py` | 新建 | `MiniMaxAnthropicLLM` 完整实现 |
| `nexus/llm/providers/__init__.py` | 修改 | 导出所有 provider 类 |
| `nexus/cli/config.py` | 修改 | `CLIConfig` 新增 `log_dir`/`log_level` 字段 |

## Assumptions & Decisions

1. **Anthropic SDK 依赖**: 使用 `anthropic>=0.39.0`，这是 Anthropic 官方 Python SDK。MiniMax 的 `/anthropic` 端点完全兼容 Anthropic Messages API 格式，可直接使用此 SDK。
2. **MiniMax 默认模型**: `MiniMax-Text-01` — 这是 MiniMax 通过 Anthropic 兼容接口暴露的旗舰模型。
3. **API Key 环境变量**: MiniMax provider 从 `MINIMAX_API_KEY` 环境变量读取（与 OpenAI 的 `OPENAI_API_KEY` 独立）。
4. **日志文件命名**: 使用 `nexus.log`（单文件 + RotatingFileHandler 轮转），而非按日期命名 — 简化实现且 `RotatingFileHandler` 已提供 `backupCount` 控制。
5. **日志文件始终 DEBUG 级别**: 文件日志始终记录 DEBUG 级别全部日志，终端级别由 verbose/debug 控制。确保排查问题时始终有完整日志。
6. **配置文件不保存 api_key**: 安全考量 — 密钥只通过环境变量或命令行传入，不写入明文配置文件。
7. **CLIConfig 默认值处理**: `save_config()` 只写入用户显式覆盖的字段，与默认值相同的字段不输出 — 减少配置文件噪音。
8. **provider 工厂函数的 enivornment key 约定**: 
   - `openai` -> `OPENAI_API_KEY`
   - `minimax` -> `MINIMAX_API_KEY`

## Verification

1. **配置文件写入**: `python -m pytest tests/test_cli_config.py -k save -v` — 验证 `save_config()` 正确写入 JSON、不写入默认值字段、不写入 api_key、目录自动创建
2. **日志持久化**: 运行 `nexus` 后检查 `~/.nexus/logs/nexus.log` 存在且有内容
3. **MiniMax Provider**: `python -m pytest tests/ -k minimax -v` — 单元测试验证 chat/stream_chat 格式转换、异常处理
4. **Provider 工厂**: 验证 `_create_llm(config.provider="minimax")` 返回 `MiniMaxAnthropicLLM` 实例
5. **全量测试**: `python -m pytest tests/` — 确保现有 114 个测试不受影响
