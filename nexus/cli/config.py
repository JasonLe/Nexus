"""Nexus CLI 配置管理 —— YAML 配置文件 + 四级加载系统。

设计思路
--------
配置文件采用 YAML 格式（nexus.yaml），支持注释和结构化嵌套。
按项目级 → 用户级 → 环境变量 → 命令行参数的优先级逐级合并。

配置文件结构 (nexus.yaml)
--------------------------

  providers:                       # LLM Provider 配置
    openai:
      api_key: sk-xxx              # 明文 API Key（save_config 写入时自动设为 0600 权限）
      # 也可使用环境变量引用: api_key: ${OPENAI_API_KEY}
      model: gpt-4o-mini
      max_tokens: 4096
      context_window_tokens: 128000
      base_url: null
    anthropic:
      api_key: sk-ant-xxx
      model: claude-sonnet-4-20250514
      max_tokens: 4096
      context_window_tokens: 200000
    minimax:
      api_key: sk-minimax-xxx
      model: MiniMax-Text-01
      max_tokens: 4096
      context_window_tokens: 245760
      base_url: https://api.minimaxi.com/anthropic

  default_provider: openai         # 默认 Provider

  agent:
    system_prompt: "You are a helpful coding assistant."
    max_steps: 30

  tools:                            # 启用的工具列表
    enabled:
      - read_file
      - write_file
      - list_dir
      - search_content

环境变量引用（向后兼容）:
  ${VAR}        - 替换为环境变量值
  ${VAR:-def}   - 有默认值的替换
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from nexus.logging import get_logger
try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = get_logger(__name__)

# ------------------------------------------------------------------
# 数据类
# ------------------------------------------------------------------


@dataclass
class ProviderConfig:
    """单个 Provider 的配置。"""
    api_key: str | None = None
    model: str = ""
    max_tokens: int = 4096
    context_window_tokens: int = 0   # 0 = 未知
    base_url: str | None = None


@dataclass
class AgentConfig:
    """Agent 级别的配置。"""
    system_prompt: str = "You are a helpful coding assistant."
    max_steps: int = 30


@dataclass
class ToolsConfig:
    """工具启用配置。enabled 为空列表表示全部启用（默认行为）。"""
    enabled: list[str] = field(default_factory=list)


@dataclass
class NexusConfig:
    """Nexus 聚合配置 —— 完整配置文件的内存表示。

    使用 load_config() 创建实例，支持从 YAML、环境变量、命令行参数
    三级来源合并配置。配置修改后可通过 save_config() 写回文件。
    """

    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    default_provider: str = "openai"
    agent: AgentConfig = field(default_factory=AgentConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)

    # 运行时字段（不从配置文件直接写入，由 CLI 或 load_config 动态设置）
    verbose: bool = False
    debug: bool = False
    work_dir: str = ""
    log_dir: str = ""

    # ---- 便捷方法 ----

    @property
    def current_provider(self) -> str:
        """当前生效的 provider 名称。"""
        return self.default_provider

    @property
    def provider_config(self) -> ProviderConfig:
        """当前 provider 的配置。"""
        p = self.providers.get(self.default_provider)
        if p is None:
            return ProviderConfig()
        return p

    @property
    def model(self) -> str:
        """当前生效的模型名称。"""
        return self.provider_config.model

    @property
    def api_key(self) -> str | None:
        """当前 provider 的 API Key。"""
        return self.provider_config.api_key

    @property
    def base_url(self) -> str | None:
        """当前 provider 的 base_url。"""
        return self.provider_config.base_url

    @property
    def max_tokens(self) -> int:
        """当前 provider 的 max_tokens。"""
        return self.provider_config.max_tokens

    @property
    def system_prompt(self) -> str:
        """Agent 系统提示词。"""
        return self.agent.system_prompt

    @property
    def max_steps(self) -> int:
        """最大执行步数。"""
        return self.agent.max_steps


# ------------------------------------------------------------------
# 默认值
# ------------------------------------------------------------------

def _default_providers() -> dict[str, ProviderConfig]:
    """返回内置的默认 Provider 配置。"""
    return {
        "openai": ProviderConfig(
            model="gpt-4o-mini",
            max_tokens=4096,
            context_window_tokens=128000,
        ),
        "anthropic": ProviderConfig(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            context_window_tokens=200000,
        ),
        "minimax": ProviderConfig(
            model="MiniMax-Text-01",
            max_tokens=4096,
            context_window_tokens=245760,
            base_url="https://api.minimaxi.com/anthropic",
        ),
    }


# ------------------------------------------------------------------
# 环境变量引用解析
# ------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _resolve_env_refs(value: str) -> str:
    """解析字符串中的 ${VAR} 和 ${VAR:-default} 引用。"""
    def _replacer(m: re.Match) -> str:
        var_name = m.group(1)
        default = m.group(2)
        env_val = os.getenv(var_name)
        if env_val is not None:
            return env_val
        if default is not None:
            return default
        return m.group(0)  # 保持原样
    return _ENV_VAR_RE.sub(_replacer, value)


# ------------------------------------------------------------------
# YAML 加载
# ------------------------------------------------------------------

def _load_yaml_config(path: str) -> dict[str, Any]:
    """加载 YAML 配置文件。文件不存在或解析失败返回 {}。"""
    if yaml is None:
        logger.warning("pyyaml not installed, YAML config support disabled")
        return {}
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning("Config file is not a mapping: %s", path)
            return {}
        return data
    except Exception as e:
        logger.warning("Failed to parse config file %s: %s", path, e)
        return {}


def _load_json_config(path: str) -> dict[str, Any]:
    """兼容旧的 JSON 配置文件。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


# ------------------------------------------------------------------
# 配置解析
# ------------------------------------------------------------------

def _parse_providers(raw: dict[str, Any]) -> dict[str, ProviderConfig]:
    """将 YAML 的 providers 节解析为 ProviderConfig 字典。"""
    result: dict[str, ProviderConfig] = {}
    for name, data in raw.items():
        if not isinstance(data, dict):
            continue
        # 解析环境变量引用
        api_key_raw = data.get("api_key")
        api_key = _resolve_env_refs(api_key_raw) if isinstance(api_key_raw, str) else api_key_raw

        result[name] = ProviderConfig(
            api_key=api_key,
            model=str(data.get("model", "")),
            max_tokens=int(data.get("max_tokens", 4096)),
            context_window_tokens=int(data.get("context_window_tokens", 0)),
            base_url=data.get("base_url"),
        )
    return result


def _find_project_config(start_dir: str, filename: str) -> str | None:
    """从 start_dir 向上查找配置文件，遇到 .git 边界停止。

    查找顺序：start_dir → parent → ... 直至遇到目标文件、.git 目录（项目根
    边界）或文件系统根。这样无论 debug 时 cwd 落在项目哪个子目录，都能读到
    项目根的 nexus.yaml。找到返回绝对路径，找不到返回 None。
    """
    current = Path(start_dir).resolve()
    for parent in [current] + list(current.parents):
        candidate = parent / filename
        if candidate.is_file():
            return str(candidate)
        # 遇到 .git 视为项目根边界：根目录都没有目标文件则停止向上查找
        if (parent / ".git").exists():
            return None
    return None


def _merge_dict_deep(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 的值覆盖 base。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dict_deep(result[key], value)
        else:
            result[key] = value
    return result


def load_config(
    cli_args: dict[str, Any] | None = None,
    work_dir: str | None = None,
) -> NexusConfig:
    """加载配置，合并四级来源。

    合并顺序（后加载覆盖前加载）：
    1. 代码默认值
    2. 用户级配置 ~/.nexus/nexus.yaml（向后兼容 ~/.nexus/config.json）
    3. 项目级配置 —— 从 work_dir 向上查找 nexus.yaml 直到 .git 边界
       （向后兼容 .nexus.json），让 debug 时 cwd 在子目录也能读到项目根配置
    4. 环境变量（NEXUS_MODEL / NEXUS_API_KEY / NEXUS_BASE_URL / NEXUS_PROVIDER 等）
    5. 命令行参数（cli_args），最高优先级

    Returns
    -------
    NexusConfig
        合并完成后的配置实例。
    """
    # 1. 默认值
    config = NexusConfig(providers=_default_providers())

    # 2. 用户级配置
    _apply_file_config(config, Path.home() / ".nexus" / "nexus.yaml")
    # 向后兼容旧的 JSON 格式
    _apply_json_config(config, Path.home() / ".nexus" / "config.json")

    # 3. 项目级配置 —— 从 work_dir 向上查找直到 .git 边界
    if work_dir:
        yaml_path = _find_project_config(work_dir, "nexus.yaml")
        if yaml_path:
            _apply_file_config(config, Path(yaml_path))
            logger.debug("Project config found: %s", yaml_path)
        json_path = _find_project_config(work_dir, ".nexus.json")
        if json_path:
            _apply_json_config(config, Path(json_path))

    # 4. 环境变量
    _apply_env_overrides(config)

    # 5. 命令行参数
    if cli_args:
        _apply_cli_overrides(config, cli_args)

    logger.debug(
        "Config loaded: provider=%s, model=%s, max_steps=%d",
        config.default_provider,
        config.model,
        config.max_steps,
    )
    return config


def _apply_file_config(config: NexusConfig, path: Path) -> None:
    """从 YAML 文件加载并合并配置。"""
    raw = _load_yaml_config(str(path))
    if not raw:
        return

    # providers —— 内联解析以便判断 YAML 中是否显式声明某字段
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

    # default_provider
    if "default_provider" in raw:
        config.default_provider = raw["default_provider"]

    # agent
    if "agent" in raw and isinstance(raw["agent"], dict):
        agent_raw = raw["agent"]
        if "system_prompt" in agent_raw:
            config.agent.system_prompt = str(agent_raw["system_prompt"])
        if "max_steps" in agent_raw:
            config.agent.max_steps = int(agent_raw["max_steps"])

    # tools
    if "tools" in raw and isinstance(raw["tools"], dict):
        if "enabled" in raw["tools"] and isinstance(raw["tools"]["enabled"], list):
            config.tools.enabled = [str(t) for t in raw["tools"]["enabled"]]

    logger.debug("Loaded config from %s", path)


def _apply_json_config(config: NexusConfig, path: Path) -> None:
    """兼容旧的 JSON 配置文件格式。"""
    raw = _load_json_config(str(path))
    if not raw:
        return
    # 旧格式: {"provider": "openai", "model": "gpt-4o", ...}
    if "provider" in raw:
        config.default_provider = str(raw["provider"])
    if "model" in raw and config.default_provider in config.providers:
        config.providers[config.default_provider].model = str(raw["model"])
    if "api_key" in raw and config.default_provider in config.providers:
        config.providers[config.default_provider].api_key = raw["api_key"]
    if "base_url" in raw and config.default_provider in config.providers:
        config.providers[config.default_provider].base_url = raw["base_url"]
    if "system_prompt" in raw:
        config.agent.system_prompt = str(raw["system_prompt"])
    if "max_steps" in raw:
        config.agent.max_steps = int(raw["max_steps"])


def _apply_env_overrides(config: NexusConfig) -> None:
    """将环境变量覆盖到当前 provider 的配置。"""
    env_mappings: dict[str, tuple[str, str]] = {
        "NEXUS_MODEL": ("model", "str"),
        "NEXUS_API_KEY": ("api_key", "str"),
        "NEXUS_BASE_URL": ("base_url", "str"),
        "NEXUS_MAX_STEPS": ("max_steps", "int"),
        "NEXUS_PROVIDER": ("provider", "str"),
        "NEXUS_MAX_TOKENS": ("max_tokens", "int"),
    }
    provider = config.providers.get(config.default_provider)
    if provider is None:
        return

    for env_key, (field, typ) in env_mappings.items():
        env_value = os.getenv(env_key)
        if env_value is None:
            continue
        if field == "provider":
            config.default_provider = env_value
        elif field == "max_steps":
            try:
                config.agent.max_steps = int(env_value)
            except ValueError:
                pass
        elif typ == "int":
            try:
                setattr(provider, field, int(env_value))
            except ValueError:
                pass
        else:
            setattr(provider, field, env_value)


def _apply_cli_overrides(config: NexusConfig, cli_args: dict[str, Any]) -> None:
    """将命令行参数覆盖到配置。"""
    provider = config.providers.get(config.default_provider)
    if provider is None:
        return

    # 直接映射
    str_mappings = {
        "model": "model",
        "api_key": "api_key",
        "base_url": "base_url",
        "system_prompt": "system_prompt",
        "provider": "provider",
    }
    for cli_key, target in str_mappings.items():
        if cli_key in cli_args and cli_args[cli_key] is not None:
            if target == "provider":
                config.default_provider = cli_args[cli_key]
            elif target == "system_prompt":
                config.agent.system_prompt = cli_args[cli_key]
            else:
                p = config.providers.get(config.default_provider)
                if p:
                    setattr(p, target, cli_args[cli_key])

    if "max_steps" in cli_args and cli_args["max_steps"] is not None:
        config.agent.max_steps = cli_args["max_steps"]
    if "max_tokens" in cli_args and cli_args["max_tokens"] is not None:
        p = config.providers.get(config.default_provider)
        if p:
            p.max_tokens = cli_args["max_tokens"]
    if "verbose" in cli_args:
        config.verbose = cli_args["verbose"]
    if "debug" in cli_args:
        config.debug = cli_args["debug"]
    if "work_dir" in cli_args and cli_args["work_dir"] is not None:
        config.work_dir = cli_args["work_dir"]


# ------------------------------------------------------------------
# 配置保存
# ------------------------------------------------------------------

def save_config(config: NexusConfig, path: str | None = None) -> str:
    """将当前配置保存为 YAML 文件。

    设计思路：与 load_config() 对称，让用户可以将运行时参数持久化。
    写入策略：生成结构化 YAML，跳过 api_key（安全），不含空值、跳过全默认的 section。

    Parameters
    ----------
    config : NexusConfig
        当前配置实例。
    path : str | None
        目标文件路径，默认 ~/.nexus/nexus.yaml。

    Returns
    -------
    str
        写入的文件路径。
    """
    if yaml is None:
        raise RuntimeError("pyyaml is required to save YAML config")

    if path is None:
        path = str(Path.home() / ".nexus" / "nexus.yaml")

    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    data: dict[str, Any] = {}

    # providers（保留 api_key —— 用户手动管理的配置需要可持久化；
    # 文件权限由本函数末尾统一设为 0o600 以降低明文 key 的风险）
    providers_data: dict[str, Any] = {}
    for name, pc in config.providers.items():
        pd: dict[str, Any] = {"model": pc.model}
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

    data["default_provider"] = config.default_provider

    # agent
    data["agent"] = {
        "system_prompt": config.agent.system_prompt,
        "max_steps": config.agent.max_steps,
    }

    # tools
    if config.tools.enabled:
        data["tools"] = {"enabled": config.tools.enabled}

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data, f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    # 配置文件可能包含明文 api_key，限制为属主可读写（POSIX 生效，Windows 上为 no-op）
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


# ------------------------------------------------------------------
# 模板生成
# ------------------------------------------------------------------

def generate_config_template() -> str:
    """生成带注释的完整 nexus.yaml 模板字符串。

    与 save_config() 的区别：模板包含所有字段（含 api_key 占位符、
    max_tokens 默认值、context_window_tokens 等），并带行内注释说明，
    供 --init-config 命令写入磁盘供用户参考填写。
    """
    return """\
# Nexus CLI 配置文件
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
