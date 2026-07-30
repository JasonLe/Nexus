# 修复配置文件初始化与读取逻辑不匹配问题

## Summary

Nexus CLI 的配置文件存在"初始化生成的配置"与"读取的配置"不匹配的问题。核心矛盾在于 `--init-config` 复用 `save_config()` 生成模板，而 `save_config()` 出于安全策略跳过 `api_key` 和默认值字段，导致生成的模板不完整、缺 `api_key` 示例；同时 README/spec 文档仍停留在旧的"三级 + JSON"设计，与实际"五级 + YAML"实现严重脱节。

本次修复目标：让 `--init-config` 生成**完整、带注释、可直接填写**的 YAML 模板（含明文 `api_key` 占位符），让 `save_config` 能够持久化用户手动填写的 `api_key`，并同步更新 README/spec 文档。

## Current State Analysis

### 配置文件加载链路（`nexus/cli/config.py`）

`load_config()` 实际加载顺序（五级）：
1. 代码默认值 `_default_providers()`
2. 用户级 `~/.nexus/nexus.yaml`（向后兼容 `~/.nexus/config.json`）
3. 项目级 `<work_dir>/nexus.yaml`（向后兼容 `<work_dir>/.nexus.json`）
4. 环境变量（`NEXUS_*`）
5. 命令行参数 `cli_args`

### 配置文件生成链路（`nexus/cli/main.py`）

两个入口：
- `--init-config`（main.py:281-292）：生成模板到 `os.getcwd()/nexus.yaml`，**复用 `save_config()`**
- `--save-config`（main.py:344-348）：保存当前运行配置到 `~/.nexus/nexus.yaml`，**复用 `save_config()`**

### 已识别的不匹配问题

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P1 | `--init-config` 复用 `save_config()`，后者跳过 `api_key` → 模板缺 `api_key` 字段示例 | main.py:290 + config.py:497 | 用户不知道如何配置 api_key |
| P2 | `save_config()` 跳过 `max_tokens==4096` 等默认值 → 模板缺字段 | config.py:501-506 | 模板不完整，无参考价值 |
| P3 | `--init-config` 有死代码：第一个 `config = NexusConfig(providers={})` 立即被覆盖，注释"用默认值生成完整模板"也错误 | main.py:282-285 | 可读性差 |
| P4 | `save_config()` 跳过所有 `api_key`，用户手动填写的 key 无法用 `--save-config` 持久化 | config.py:497 | 往返丢失 api_key |
| P5 | README 配置管理章节写"三级 + JSON"，实际是"五级 + YAML" | README.md:632-652 | 文档严重误导 |
| P6 | README 命令参考表缺 `--init-config`/`--save-config`/`--max-tokens`/`--provider` | README.md:604-619 | 用户不知道有这些命令 |
| P7 | spec.md 配置文件格式仍是旧 JSON 设计 | spec.md:66-83 | 与实现不符 |
| P8 | `_apply_file_config` 的 `max_tokens` 合并逻辑绕且脆弱 | config.py:341 | 可维护性差 |

## Proposed Changes

### 决策依据（用户确认）

> 用户希望"用户最初的 api 相关所有的配置都手动写在配置文件里，而不是用环境变量"。

由此确定：
- `--init-config` 模板：`api_key` 用**明文占位符**（如 `api_key: null  # 填入你的 OpenAI API Key`），不用 `${ENV_VAR}` 引用
- `save_config`：**保留** api_key（用户手动填写，需可持久化），并加文件权限保护
- `${ENV_VAR}` 解析功能保留（向后兼容），但模板默认不使用
- 修复范围**包含** README.md 和 spec.md 文档同步

---

### 改动 1：新增 `generate_config_template()` 函数

**文件**：`nexus/cli/config.py`
**位置**：在 `save_config()` 之后新增
**原因**：模板生成与配置保存是两个语义——模板需要完整带注释，保存需要精简。不应共用 `save_config()`。

新增函数：

```python
def generate_config_template() -> str:
    """生成带注释的完整 nexus.yaml 模板字符串。

    与 save_config() 的区别：模板包含所有字段（含 api_key 占位符、
    max_tokens 默认值、context_window_tokens 等），并带行内注释说明，
    供 --init-config 命令写入磁盘供用户参考填写。
    """
    return """\
# Nexus CLI 配置文件
# 完整字段参考 https://github.com/.../nexus/cli/config.py 顶部文档
# 优先级：命令行参数 > 环境变量 > 项目级 nexus.yaml > 用户级 ~/.nexus/nexus.yaml > 默认值

providers:
  openai:
    api_key: null              # 填入你的 OpenAI API Key，例如 sk-xxx
    model: gpt-4o-mini
    max_tokens: 4096
    context_window_tokens: 128000
    base_url: null             # 自定义 API 端点，留空使用官方端点
  anthropic:
    api_key: null              # 填入你的 Anthropic API Key
    model: claude-sonnet-4-20250514
    max_tokens: 4096
    context_window_tokens: 200000
    base_url: null
  minimax:
    api_key: null              # 填入你的 MiniMax API Key
    model: MiniMax-Text-01
    max_tokens: 4096
    context_window_tokens: 245760
    base_url: https://api.minimaxi.com/anthropic

default_provider: openai       # 默认使用的 provider

agent:
  system_prompt: "You are a helpful coding assistant."
  max_steps: 30

tools:
  enabled:
    - read_file
    - write_file
    - list_dir
    - search_content
"""
```

### 改动 2：重写 `--init-config` 命令逻辑

**文件**：`nexus/cli/main.py`
**位置**：第 281-292 行
**原因**：去除死代码（P3），改用 `generate_config_template()` 生成完整模板（P1、P2）。

替换为：

```python
# --init-config 生成完整带注释的模板后退出
if args.init_config:
    from nexus.cli.config import generate_config_template
    path = str(Path(os.getcwd()) / "nexus.yaml")
    if os.path.exists(path):
        print(f"File already exists: {path}")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(generate_config_template())
    print(f"Config template created: {path}")
    print("Fill in your API keys, then run `nexus` to start.")
    return
```

注意：移除 `main.py:284` 的 `from nexus.cli.config import _default_providers` 局部导入（不再需要）。

### 改动 3：修改 `save_config()` 保留 api_key 并加文件权限保护

**文件**：`nexus/cli/config.py`
**位置**：`save_config()` 第 497-531 行
**原因**：用户要求手动填写的 api_key 能持久化（P4），同时用文件权限降低明文 key 的风险。

改动点：
1. **保留 api_key**：写入 providers 时不再跳过 api_key（删除"跳过 api_key（安全）"的注释和实现）
2. **文件权限保护**：写入后对文件设置 `0o600` 权限（仅属主可读写）
3. **安全警告日志**：当写入的配置包含非空 api_key 时，打印一次 debug 日志提示用户注意文件权限

修改后的 providers 序列化段：

```python
# providers（保留 api_key，用户手动管理的配置需要可持久化）
providers_data: dict[str, Any] = {}
for name, pc in config.providers.items():
    pd: dict[str, Any] = {"model": pc.model}
    # api_key：保留用户填写的值（明文）。文件权限由本函数统一设为 0o600。
    if pc.api_key:
        pd["api_key"] = pc.api_key
    if pc.max_tokens:
        pd["max_tokens"] = pc.max_tokens
    if pc.context_window_tokens:
        pd["context_window_tokens"] = pc.context_window_tokens
    if pc.base_url:
        pd["base_url"] = pc.base_url
    providers_data[name] = pd
if providers_data:
    data["providers"] = providers_data
```

文件写入后新增权限设置：

```python
with open(path, "w", encoding="utf-8") as f:
    yaml.safe_dump(
        data, f,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )

# 配置文件可能包含明文 api_key，限制为属主可读写
try:
    os.chmod(path, 0o600)
except OSError:
    logger.debug("Failed to chmod 600 on %s", path)

has_api_key = any(pc.api_key for pc in config.providers.values())
if has_api_key:
    logger.debug("Config saved with api_key to %s (file permission 0600)", path)
else:
    logger.info("Config saved to %s", path)
return path
```

### 改动 4：简化 `_apply_file_config` 的 max_tokens 合并逻辑

**文件**：`nexus/cli/config.py`
**位置**：第 324-367 行
**原因**：P8，原逻辑 `if pc.max_tokens != 4096 or "max_tokens" in raw...` 绕且脆弱。

简化为：只要 YAML 显式提供了该字段就覆盖（`_parse_providers` 已经用 `data.get("max_tokens", 4096)` 处理缺省）。需要让 `_parse_providers` 能区分"用户显式写了 4096"和"未写"。

实现方式：在 `_parse_providers` 中只读取 YAML 里实际存在的字段（用 `in` 判断），返回的 `ProviderConfig` 只包含 YAML 显式声明的值，其余保持 None/0 的"未设置"哨兵。然后 `_apply_file_config` 统一用"非空才覆盖"的规则合并。

具体改动：
- `_parse_providers`（第 246-263 行）：`max_tokens` 改为 `data.get("max_tokens")`，仅在非 None 时转 int；未提供时保持 `ProviderConfig` 默认值 `4096`，但用一个新哨兵字段或直接依赖"非空覆盖"语义。

为避免引入新字段（保持 dataclass 简洁），采用更直接的方案：**在 `_apply_file_config` 里直接读 raw dict 判断字段是否存在**，不再依赖 `pc.max_tokens != 4096` 这种脆弱比较。

简化后的 providers 合并段：

```python
# providers
if "providers" in raw and isinstance(raw["providers"], dict):
    for name, prov_raw in raw["providers"].items():
        if not isinstance(prov_raw, dict):
            continue
        # 解析环境变量引用（向后兼容 ${VAR} 语法）
        api_key_raw = prov_raw.get("api_key")
        api_key = _resolve_env_refs(api_key_raw) if isinstance(api_key_raw, str) else api_key_raw

        if name in config.providers:
            existing = config.providers[name]
            # 仅覆盖 YAML 中显式声明的字段
            if api_key is not None:
                existing.api_key = api_key
            if "model" in prov_raw:
                existing.model = str(prov_raw["model"])
            if "max_tokens" in prov_raw:
                existing.max_tokens = int(prov_raw["max_tokens"])
            if "context_window_tokens" in prov_raw:
                existing.context_window_tokens = int(prov_raw["context_window_tokens"])
            if "base_url" in prov_raw and prov_raw["base_url"] is not None:
                existing.base_url = prov_raw["base_url"]
        else:
            # 新增 provider，走完整解析
            config.providers[name] = ProviderConfig(
                api_key=api_key,
                model=str(prov_raw.get("model", "")),
                max_tokens=int(prov_raw.get("max_tokens", 4096)),
                context_window_tokens=int(prov_raw.get("context_window_tokens", 0)),
                base_url=prov_raw.get("base_url"),
            )
    logger.debug("Loaded config from %s", path)
```

注意：此改动移除了对 `_parse_providers` 的调用（在 `_apply_file_config` 上下文里），但 `_parse_providers` 函数本身保留（可能被其他地方或测试引用，且 test_cli_config.py 直接测试了合并行为）。需确认 `_parse_providers` 是否仍被使用——经检查，仅 `_apply_file_config` 调用，但保留函数不删除（避免破坏外部导入）。实际上 grep 一下确认无其他调用后可保留为内部 helper。

### 改动 5：更新 config.py 顶部文档字符串

**文件**：`nexus/cli/config.py`
**位置**：第 1-46 行的模块 docstring
**原因**：文档示例里 `api_key: ${OPENAI_API_KEY}` 用了环境变量引用，与新决策（模板用明文）方向不一致。保留 `${VAR}` 语法说明（功能仍在），但把示例改成明文 + 注释说明也可用环境变量引用。

修改后的配置文件结构示例段：

```
配置文件结构 (nexus.yaml)
--------------------------

  providers:                       # LLM Provider 配置
    openai:
      api_key: sk-xxx              # 明文 API Key（推荐配合文件权限 0600）
      # 也可使用环境变量引用: api_key: ${OPENAI_API_KEY}
      model: gpt-4o-mini
      max_tokens: 4096
      context_window_tokens: 128000
      base_url: null
    ...
```

### 改动 6：更新 README.md 配置管理章节

**文件**：`README.md`
**位置**：第 632-652 行"配置管理"章节
**原因**：P5，文档与实现严重脱节。

替换为：

```markdown
### 配置管理

支持五级配置（优先级从高到低）：

1. **命令行参数**：`--model gpt-4o --api-key sk-xxx --provider openai`
2. **环境变量**：`NEXUS_MODEL`、`NEXUS_API_KEY`、`NEXUS_BASE_URL`、`NEXUS_PROVIDER`、`NEXUS_MAX_STEPS`、`NEXUS_MAX_TOKENS`
3. **项目级配置文件**：`<work_dir>/nexus.yaml`（向后兼容 `.nexus.json`）
4. **用户级配置文件**：`~/.nexus/nexus.yaml`（向后兼容 `~/.nexus/config.json`）
5. **内置默认值**：openai/anthropic/minimax 三个 provider 的默认模型与参数

#### 生成配置模板

```bash
# 在当前目录生成 nexus.yaml 模板（含注释和所有字段示例）
nexus --init-config
```

模板生成后，手动填入各 provider 的 `api_key` 字段即可。出于安全考虑，`save_config` 写入的文件会自动设置 `0600` 权限。

#### 保存当前运行配置

```bash
# 退出前将当前生效配置写入 ~/.nexus/nexus.yaml
nexus --save-config
```

配置文件示例 (`nexus.yaml`)：

```yaml
providers:
  openai:
    api_key: sk-xxx
    model: gpt-4o-mini
    max_tokens: 4096
    context_window_tokens: 128000
    base_url: null
  anthropic:
    api_key: sk-ant-xxx
    model: claude-sonnet-4-20250514
    max_tokens: 4096
    context_window_tokens: 200000

default_provider: openai

agent:
  system_prompt: "You are a helpful coding assistant."
  max_steps: 30

tools:
  enabled:
    - read_file
    - write_file
    - list_dir
    - search_content
```

> 也支持 `${ENV_VAR}` 和 `${ENV_VAR:-default}` 环境变量引用语法（向后兼容）。
```

### 改动 7：补全 README.md 命令参考表

**文件**：`README.md`
**位置**：第 604-619 行命令行参数表
**原因**：P6，缺 `--init-config`/`--save-config`/`--max-tokens`/`--provider`。

在表格中补充：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--provider` | LLM Provider (openai\|anthropic\|minimax) | `openai` |
| `--max-tokens` | 单次 LLM 调用最大输出 token 数 | `4096` |
| `--init-config` | 在当前目录生成 `nexus.yaml` 模板 | - |
| `--save-config` | 退出前将当前配置写入 `~/.nexus/nexus.yaml` | - |

### 改动 8：更新 spec.md 配置文件格式

**文件**：`.trae/specs/add-nexus-cli/spec.md`
**位置**：第 66-83 行"配置管理"小节
**原因**：P7，spec 仍是旧 JSON 设计。

把 JSON 示例改为 YAML 示例，并更新级别描述为五级。与改动 6 的 README 内容保持一致（spec 是历史规格文档，更新其配置格式说明即可，不改 spec 的其他结构）。

## Assumptions & Decisions

1. **保留 `${ENV_VAR}` 解析功能**：`_resolve_env_refs` 函数和 `_ENV_VAR_RE` 正则保留，向后兼容已有配置文件。仅模板默认改用明文。
2. **保留旧 JSON 向后兼容**：`_load_json_config` 和 `_apply_json_config` 保留不动。
3. **`_parse_providers` 函数保留**：虽然 `_apply_file_config` 不再调用它（改为内联解析以便判断字段是否存在），但保留该函数避免破坏可能的导入。
4. **文件权限 `0600`**：仅在 POSIX 系统生效，Windows 上 `os.chmod` 是 no-op，不影响功能。
5. **不改 `--init-config` 与 `--save-config` 的默认路径**：`--init-config` → 项目级（cwd），`--save-config` → 用户级（~/.nexus）。这是两个命令的语义差异（生成模板 vs 持久化运行配置），保留现状。
6. **不引入新依赖**：仅用标准库 `os.chmod`。
7. **测试同步更新**：`tests/test_cli_config.py` 中 `TestInitConfig` 和 `TestSaveConfig` 需要更新断言（api_key 现在被保留，不再断言 "api_key not in content"）。

## Verification Steps

1. **运行测试**：`cd /Users/wanghanle/Documents/code/githubProject/Nexus && python -m pytest tests/test_cli_config.py -v`
   - 预期：所有测试通过（更新后的断言）
2. **手动验证 --init-config**：
   ```bash
   cd /tmp && rm -f nexus.yaml
   python -m nexus.cli --init-config
   cat nexus.yaml
   ```
   - 预期：生成的 `nexus.yaml` 包含 `api_key: null` 占位符、所有 provider、agent、tools 字段，带注释
3. **手动验证 --save-config 保留 api_key**：
   ```bash
   cd /tmp && python -m nexus.cli --api-key sk-test-123 --save-config "test"
   cat ~/.nexus/nexus.yaml
   ls -l ~/.nexus/nexus.yaml  # 检查权限为 0600
   ```
   - 预期：保存的文件包含 `api_key: sk-test-123`，文件权限 `-rw-------`
4. **往返测试**：`save_config` → `load_config` 往返后 api_key 不丢失
5. **检查 README 渲染**：确认配置管理章节和命令参考表显示正确

## Files Changed

| 文件 | 改动类型 |
|------|---------|
| `nexus/cli/config.py` | 新增 `generate_config_template()`；修改 `save_config()`（保留 api_key + chmod 600）；简化 `_apply_file_config` providers 合并；更新顶部 docstring |
| `nexus/cli/main.py` | 重写 `--init-config` 逻辑（改用 `generate_config_template`，删除死代码） |
| `tests/test_cli_config.py` | 更新 `TestInitConfig` 和 `TestSaveConfig` 断言（api_key 现在被保留） |
| `README.md` | 更新配置管理章节（JSON→YAML、三级→五级）、补全命令参考表 |
| `.trae/specs/add-nexus-cli/spec.md` | 更新配置文件格式说明（JSON→YAML） |
