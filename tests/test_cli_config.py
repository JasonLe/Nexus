"""测试配置管理系统：CLIConfig、_load_json_config、_merge_config、load_config。

覆盖范围
--------
- CLIConfig 默认值正确性
- 环境变量覆盖默认值（NEXUS_MODEL, NEXUS_API_KEY 等）
- 命令行参数优先级高于环境变量
- 用户级配置文件 ~/.nexus/config.json 加载
- 项目级 .nexus.json 覆盖用户级配置
- 部分配置文件不影响其他字段的默认值
- 配置文件不存在时不报错
- JSON 格式错误的配置文件返回默认值
"""

import json
import os
from pathlib import Path

import pytest

from nexus.cli.config import CLIConfig, _load_json_config, _merge_config, load_config


# ---------------------------------------------------------------------------
# CLIConfig 默认值测试
# ---------------------------------------------------------------------------


class TestCLIConfigDefaults:
    """测试 CLIConfig 默认值。"""

    def test_default_config(self):
        """无任何参数时应返回所有默认值。"""
        config = CLIConfig()
        assert config.model == "gpt-4o-mini"
        assert config.provider == "openai"
        assert config.api_key is None
        assert config.base_url is None
        assert config.system_prompt == "You are a helpful coding assistant."
        assert config.max_steps == 30
        assert config.work_dir is None
        assert config.verbose is False
        assert config.debug is False


# ---------------------------------------------------------------------------
# _load_json_config 测试
# ---------------------------------------------------------------------------


class TestLoadJsonConfig:
    """测试 _load_json_config 纯函数。"""

    def test_valid_config_file(self, tmp_path):
        """加载有效的 JSON 配置文件应返回对应的字典。"""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps({"model": "gpt-4", "max_steps": 50}),
            encoding="utf-8",
        )
        result = _load_json_config(str(config_path))
        assert result == {"model": "gpt-4", "max_steps": 50}

    def test_missing_config_file(self, tmp_path):
        """配置文件不存在时应返回空字典，不报错。"""
        result = _load_json_config(str(tmp_path / "nonexistent.json"))
        assert result == {}

    def test_invalid_json(self, tmp_path):
        """JSON 格式错误时应返回空字典，不报错。"""
        config_path = tmp_path / "bad.json"
        config_path.write_text("{not valid json", encoding="utf-8")
        result = _load_json_config(str(config_path))
        assert result == {}

    def test_not_a_dict(self, tmp_path):
        """配置文件不是 JSON 对象时应返回空字典。"""
        config_path = tmp_path / "array.json"
        config_path.write_text("[1, 2, 3]", encoding="utf-8")
        result = _load_json_config(str(config_path))
        assert result == {}


# ---------------------------------------------------------------------------
# _merge_config 测试
# ---------------------------------------------------------------------------


class TestMergeConfig:
    """测试 _merge_config 纯函数。"""

    def test_overrides_existing_fields(self):
        """source 中的值应覆盖 target 中的对应字段。"""
        config = CLIConfig()
        _merge_config(config, {"model": "gpt-4", "verbose": True})
        assert config.model == "gpt-4"
        assert config.verbose is True

    def test_none_values_are_skipped(self):
        """source 中值为 None 的键应被忽略，不影响已有值。"""
        config = CLIConfig(model="custom-model")
        _merge_config(config, {"model": None, "api_key": None})
        assert config.model == "custom-model"  # 未被 None 覆盖

    def test_unknown_fields_are_ignored(self):
        """source 中不存在于 CLIConfig 的键应被静默忽略。"""
        config = CLIConfig()
        _merge_config(config, {"unknown_field": "value", "model": "gpt-4"})
        assert config.model == "gpt-4"
        assert not hasattr(config, "unknown_field")

    def test_partial_config_preserves_other_fields(self):
        """只覆盖部分字段时，其他字段应保持原值。"""
        config = CLIConfig()
        _merge_config(config, {"max_steps": 100})
        assert config.max_steps == 100
        # 其他字段保持不变
        assert config.model == "gpt-4o-mini"
        assert config.provider == "openai"
        assert config.api_key is None


# ---------------------------------------------------------------------------
# load_config 集成测试
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """测试 load_config 的三级配置合并。"""

    # ---------- 默认值 ----------

    def test_default_config(self):
        """无环境变量、无配置文件、无 CLI 参数时应返回默认值。"""
        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "gpt-4o-mini"
        assert config.provider == "openai"
        assert config.api_key is None
        assert config.max_steps == 30

    # ---------- 环境变量 ----------

    def test_env_vars_override(self, monkeypatch):
        """环境变量应覆盖默认值。"""
        monkeypatch.setenv("NEXUS_MODEL", "gpt-4")
        monkeypatch.setenv("NEXUS_API_KEY", "sk-test-123")
        monkeypatch.setenv("NEXUS_BASE_URL", "https://proxy.example.com")
        monkeypatch.setenv("NEXUS_MAX_STEPS", "50")

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "gpt-4"
        assert config.api_key == "sk-test-123"
        assert config.base_url == "https://proxy.example.com"
        assert config.max_steps == 50

    def test_env_max_steps_invalid(self, monkeypatch):
        """NEXUS_MAX_STEPS 为非法值时使用默认值，不崩溃。"""
        monkeypatch.setenv("NEXUS_MAX_STEPS", "not_a_number")
        config = load_config(cli_args=None, work_dir=None)
        assert config.max_steps == 30  # 回退到默认值

    # ---------- 命令行参数优先级 ----------

    def test_cli_args_priority(self, monkeypatch):
        """命令行参数应覆盖环境变量。"""
        monkeypatch.setenv("NEXUS_MODEL", "env-model")
        monkeypatch.setenv("NEXUS_API_KEY", "env-key")

        config = load_config(
            cli_args={"model": "cli-model", "api_key": "cli-key"},
            work_dir=None,
        )
        assert config.model == "cli-model"
        assert config.api_key == "cli-key"

    def test_cli_args_partial(self, monkeypatch):
        """CLI 参数只传部分字段时，其余字段仍从环境变量读取。"""
        monkeypatch.setenv("NEXUS_MODEL", "env-model")

        config = load_config(
            cli_args={"max_steps": 20},
            work_dir=None,
        )
        assert config.model == "env-model"  # 来自环境变量
        assert config.max_steps == 20       # 来自 CLI

    # ---------- 用户级配置文件 ----------

    def test_user_config_file(self, monkeypatch, tmp_path):
        """~/.nexus/config.json 应覆盖默认值。"""
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "config.json").write_text(
            json.dumps({"model": "user-model", "max_steps": 60}),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "user-model"
        assert config.max_steps == 60

    # ---------- 项目级配置文件 ----------

    def test_project_config_override(self, monkeypatch, tmp_path):
        """.nexus.json 应覆盖用户级配置。"""
        # 用户级配置
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "config.json").write_text(
            json.dumps({"model": "user-model", "max_steps": 60}),
            encoding="utf-8",
        )

        # 项目级配置
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".nexus.json").write_text(
            json.dumps({"model": "project-model"}),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=str(proj))
        assert config.model == "project-model"  # 项目级覆盖了用户级
        assert config.max_steps == 60            # 用户级未被子项目覆盖的字段仍保留

    # ---------- 部分配置 ----------

    def test_partial_config(self, monkeypatch, tmp_path):
        """配置文件只包含部分字段时，不影响其他字段的默认值。"""
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        # 只设置 model，不设置其他字段
        (nexus_dir / "config.json").write_text(
            json.dumps({"model": "partial-model"}),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "partial-model"
        assert config.provider == "openai"  # 未受影响
        assert config.max_steps == 30       # 未受影响
        assert config.api_key is None       # 未受影响

    # ---------- 文件不存在 ----------

    def test_missing_config_file(self, monkeypatch, tmp_path):
        """配置文件不存在时 load_config 不报错。"""
        home = tmp_path / "home"
        home.mkdir()
        # 不创建 .nexus 目录，也不创建 config.json

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "gpt-4o-mini"  # 使用默认值

    # ---------- JSON 格式错误 ----------

    def test_invalid_json_config(self, monkeypatch, tmp_path):
        """配置文件 JSON 格式错误时返回默认值，不崩溃。"""
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "config.json").write_text("not valid json {{{", encoding="utf-8")

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "gpt-4o-mini"  # 回退到默认值

    # ---------- 完整优先级链 ----------

    def test_full_priority_chain(self, monkeypatch, tmp_path):
        """验证完整优先级链：CLI > env > project > user > default。"""
        # 用户级配置
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "config.json").write_text(
            json.dumps({"model": "user-model", "max_steps": 60}),
            encoding="utf-8",
        )

        # 项目级配置
        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".nexus.json").write_text(
            json.dumps({"model": "project-model", "verbose": True}),
            encoding="utf-8",
        )

        # 环境变量
        monkeypatch.setenv("NEXUS_MODEL", "env-model")
        monkeypatch.setattr(Path, "home", lambda: home)

        # CLI 参数（最高优先级）
        config = load_config(
            cli_args={"model": "cli-model"},
            work_dir=str(proj),
        )

        assert config.model == "cli-model"     # CLI 最高
        assert config.verbose is True           # 项目级
        assert config.max_steps == 60           # 用户级
        assert config.provider == "openai"      # 默认值
