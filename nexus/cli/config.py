"""Nexus CLI 配置管理 —— 三级配置加载系统。

设计思路
--------
优先级从高到低：
1. 命令行参数（--model / --api-key / --base-url 等）
2. 环境变量（NEXUS_MODEL / NEXUS_API_KEY / NEXUS_BASE_URL）
3. 配置文件（项目级 .nexus.json > 用户级 ~/.nexus/config.json）
4. 代码默认值

设计原因：让用户在不同场景下灵活配置——命令行适合临时切换，
环境变量适合 CI/CD 和密钥安全，配置文件适合固定项目设置。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from nexus.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CLIConfig:
    """CLI 配置数据类 —— 聚合来自命令行、环境变量、配置文件的参数。

    所有字段均有合理的默认值，确保零配置即可运行。
    配置加载由 load_config() 按优先级逐级合并。

    Attributes
    ----------
    model : str
        LLM 模型名称，默认 ``"gpt-4o-mini"``。
    provider : str
        LLM Provider 标识，默认 ``"openai"``。
    api_key : str | None
        API 密钥，None 时由 provider 自行从环境变量读取。
    base_url : str | None
        自定义 API endpoint，用于代理或兼容网关。
    system_prompt : str
        系统提示词，注入为 messages 的第一条 system 消息。
    max_steps : int
        最大执行步数，防止无限循环，默认 30。
    work_dir : str | None
        当前工作目录，None 时使用 os.getcwd()。
    verbose : bool
        是否显示详细日志（INFO 级别）。
    debug : bool
        是否显示调试日志（DEBUG 级别）。
    """

    model: str = "gpt-4o-mini"
    provider: str = "openai"
    api_key: str | None = None
    base_url: str | None = None
    system_prompt: str = "You are a helpful coding assistant."
    max_steps: int = 30
    work_dir: str | None = None
    verbose: bool = False
    debug: bool = False


def _load_json_config(path: str) -> dict[str, Any]:
    """加载 JSON 配置文件，不存在则返回空字典。

    文件不存在或 JSON 解析失败时不会抛出异常，仅记录警告日志并返回 {}。
    这种宽松策略确保单个配置文件的缺失不会阻止程序启动。

    Parameters
    ----------
    path : str
        JSON 配置文件的绝对或相对路径。

    Returns
    -------
    dict[str, Any]
        解析后的配置字典；文件不存在或格式错误时返回空字典。
    """
    if not os.path.isfile(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("Config file is not a JSON object: %s", path)
            return {}
        return data
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse config file %s: %s", path, e)
        return {}
    except OSError as e:
        logger.warning("Failed to read config file %s: %s", path, e)
        return {}


def _merge_config(target: CLIConfig, source: dict[str, Any]) -> None:
    """将 dict 中的非 None 值覆写到 CLIConfig 对应字段。

    仅更新 source 中值为非 None 且字段名存在于 CLIConfig 中的键。
    这种"增量覆盖"策略确保空值不会意外覆盖已有配置。

    值不存在于 CLIConfig 字段集合中的键将被静默忽略。

    Parameters
    ----------
    target : CLIConfig
        待更新的配置实例。
    source : dict[str, Any]
        配置来源字典，仅非 None 值会被写入。
    """
    valid_fields = {f.name for f in fields(CLIConfig)}  # type: ignore[arg-type]
    for key, value in source.items():
        if value is not None and key in valid_fields:
            setattr(target, key, value)


def load_config(
    cli_args: dict[str, Any] | None = None,
    work_dir: str | None = None,
) -> CLIConfig:
    """加载配置，合并三级来源。

    合并顺序（后加载覆盖前加载）：
    1. 从默认值创建 CLIConfig()
    2. 用户级配置 ~/.nexus/config.json
    3. 项目级配置 <work_dir>/.nexus.json
    4. 环境变量：NEXUS_MODEL / NEXUS_API_KEY / NEXUS_BASE_URL / NEXUS_MAX_STEPS
    5. 命令行参数（cli_args），最高优先级

    Parameters
    ----------
    cli_args : dict[str, Any] | None
        argparse 解析后的参数字典，仅应包含用户显式传入的非 None 值。
        若为 None 或空，跳过命令行合并步骤。
    work_dir : str | None
        工作目录，用于查找项目级 .nexus.json。若为 None，跳过项目级配置。

    Returns
    -------
    CLIConfig
        合并完成后的配置实例。
    """
    # 1. 从默认值开始
    config = CLIConfig()

    # 2. 用户级配置：~/.nexus/config.json
    home_config_path = os.path.join(str(Path.home()), ".nexus", "config.json")
    user_config = _load_json_config(home_config_path)
    if user_config:
        _merge_config(config, user_config)
        logger.debug("Loaded user config from %s", home_config_path)

    # 3. 项目级配置：<work_dir>/.nexus.json
    if work_dir:
        project_config_path = os.path.join(work_dir, ".nexus.json")
        project_config = _load_json_config(project_config_path)
        if project_config:
            _merge_config(config, project_config)
            logger.debug("Loaded project config from %s", project_config_path)

    # 4. 环境变量
    env_mappings: dict[str, str] = {
        "NEXUS_MODEL": "model",
        "NEXUS_API_KEY": "api_key",
        "NEXUS_BASE_URL": "base_url",
        "NEXUS_MAX_STEPS": "max_steps",
    }
    for env_key, field_name in env_mappings.items():
        env_value = os.getenv(env_key)
        if env_value is not None:
            # max_steps 需要转为 int，其余保持字符串
            if field_name == "max_steps":
                try:
                    setattr(config, field_name, int(env_value))
                except ValueError:
                    logger.warning(
                        "Invalid NEXUS_MAX_STEPS value: %s, using default", env_value
                    )
            else:
                setattr(config, field_name, env_value)

    # 5. 命令行参数（最高优先级）
    if cli_args:
        _merge_config(config, cli_args)

    logger.debug(
        "Config loaded: model=%s, provider=%s, max_steps=%d, work_dir=%s",
        config.model,
        config.provider,
        config.max_steps,
        config.work_dir,
    )
    return config
