"""测试配置管理系统：NexusConfig、YAML/JSON 加载、四级合并、保存。

覆盖范围
--------
- NexusConfig 默认值正确性
- YAML 配置文件加载（_load_yaml_config）
- 环境变量覆盖（NEXUS_MODEL、NEXUS_API_KEY 等）
- 命令行参数覆盖
- Provider 切换后便捷属性跟随变化
- 旧 JSON 配置文件向后兼容
- YAML 中 providers 与默认值合并
- tools.enabled 正确加载
- --init-config 生成 YAML 模板
- save_config() 写入 YAML 文件
- NexusConfig 便捷属性（model/api_key/base_url）指向当前 provider
- 内置 openai/anthropic/minimax 三个 provider
"""

import json
import os
from pathlib import Path

import pytest
import yaml

from nexus.cli.config import (
    AgentConfig,
    NexusConfig,
    ProviderConfig,
    ToolsConfig,
    _default_providers,
    _find_project_config,
    _load_json_config,
    _load_yaml_config,
    generate_config_template,
    load_config,
    save_config,
)


# ---------------------------------------------------------------------------
# NexusConfig 默认值测试
# ---------------------------------------------------------------------------

class TestNexusConfigDefaults:
    """测试 NexusConfig 默认值。"""

    def test_default_config(self):
        """无参数创建时应使用合理的默认值。"""
        config = NexusConfig(providers=_default_providers())
        assert config.default_provider == "openai"
        assert config.model == "gpt-4o-mini"
        assert config.api_key is None
        assert config.base_url is None
        assert config.system_prompt == "You are a helpful coding assistant."
        assert config.max_steps == 30
        assert config.verbose is False
        assert config.debug is False
        assert config.work_dir == ""
        assert config.log_dir == ""


# ---------------------------------------------------------------------------
# _load_yaml_config 测试
# ---------------------------------------------------------------------------

class TestLoadYamlConfig:
    """测试 _load_yaml_config。"""

    def test_load_yaml_config_valid(self, tmp_path):
        """加载有效的 YAML 文件应返回对应字典。"""
        config_path = tmp_path / "nexus.yaml"
        config_path.write_text(
            yaml.dump({
                "default_provider": "openai",
                "providers": {
                    "openai": {"model": "gpt-4", "max_tokens": 8000},
                },
                "agent": {"max_steps": 50},
            }),
            encoding="utf-8",
        )
        result = _load_yaml_config(str(config_path))
        assert result["default_provider"] == "openai"
        assert result["providers"]["openai"]["model"] == "gpt-4"
        assert result["agent"]["max_steps"] == 50

    def test_load_yaml_config_missing(self, tmp_path):
        """文件不存在时应返回空字典。"""
        result = _load_yaml_config(str(tmp_path / "nonexistent.yaml"))
        assert result == {}

    def test_load_yaml_config_invalid(self, tmp_path):
        """YAML 解析失败时返回空字典，不崩溃。"""
        config_path = tmp_path / "bad.yaml"
        config_path.write_text(":invalid: yaml: [", encoding="utf-8")
        result = _load_yaml_config(str(config_path))
        assert result == {}

    def test_load_yaml_config_not_dict(self, tmp_path):
        """YAML 文件不是 mapping 时返回空字典。"""
        config_path = tmp_path / "list.yaml"
        config_path.write_text(yaml.dump([1, 2, 3]), encoding="utf-8")
        result = _load_yaml_config(str(config_path))
        assert result == {}


# ---------------------------------------------------------------------------
# _load_json_config 测试（向后兼容）
# ---------------------------------------------------------------------------

class TestLoadJsonConfig:
    """测试 _load_json_config 兼容旧 JSON 格式。"""

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
        """配置文件不存在时应返回空字典。"""
        result = _load_json_config(str(tmp_path / "nonexistent.json"))
        assert result == {}

    def test_invalid_json(self, tmp_path):
        """JSON 格式错误时应返回空字典。"""
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
# load_config 集成测试
# ---------------------------------------------------------------------------

class TestLoadConfig:
    """测试 load_config 的四级配置合并。"""

    # ---------- 环境变量 ----------

    def test_env_var_override(self, monkeypatch):
        """环境变量 NEXUS_MODEL 应覆盖默认值。"""
        monkeypatch.setenv("NEXUS_MODEL", "gpt-4")
        monkeypatch.setenv("NEXUS_API_KEY", "sk-test-123")
        monkeypatch.setenv("NEXUS_BASE_URL", "https://proxy.example.com")
        monkeypatch.setenv("NEXUS_MAX_STEPS", "50")
        monkeypatch.setenv("NEXUS_MAX_TOKENS", "8000")

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "gpt-4"
        assert config.api_key == "sk-test-123"
        assert config.base_url == "https://proxy.example.com"
        assert config.max_steps == 50
        assert config.max_tokens == 8000

    def test_env_max_steps_invalid(self, monkeypatch):
        """NEXUS_MAX_STEPS 为非法值时使用默认值。"""
        monkeypatch.setenv("NEXUS_MAX_STEPS", "not_a_number")
        config = load_config(cli_args=None, work_dir=None)
        assert config.max_steps == 30

    def test_env_provider_switch(self, monkeypatch):
        """NEXUS_PROVIDER 切换 provider 后 model 跟随变化。"""
        monkeypatch.setenv("NEXUS_PROVIDER", "anthropic")
        config = load_config(cli_args=None, work_dir=None)
        assert config.default_provider == "anthropic"
        assert config.model == "claude-sonnet-4-20250514"

    # ---------- 命令行参数 ----------

    def test_cli_args_override(self, monkeypatch):
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
        """CLI 参数只传部分字段时，其余从环境变量读取。"""
        monkeypatch.setenv("NEXUS_MODEL", "env-model")

        config = load_config(cli_args={"max_steps": 20}, work_dir=None)
        assert config.model == "env-model"
        assert config.max_steps == 20

    def test_cli_args_provider_switch(self, monkeypatch):
        """CLI 参数切换 provider 后 model 跟随。"""
        config = load_config(cli_args={"provider": "anthropic"}, work_dir=None)
        assert config.default_provider == "anthropic"
        assert config.model == "claude-sonnet-4-20250514"

    def test_cli_args_verbose_debug_work_dir(self):
        """CLI 参数设置运行时字段。"""
        config = load_config(
            cli_args={"verbose": True, "debug": True, "work_dir": "/tmp/test"},
            work_dir=None,
        )
        assert config.verbose is True
        assert config.debug is True
        assert config.work_dir == "/tmp/test"

    # ---------- 项目级 YAML 配置 ----------

    def test_project_yaml_config(self, tmp_path):
        """项目目录下的 nexus.yaml 应覆盖默认值。"""
        yaml_path = tmp_path / "nexus.yaml"
        yaml_path.write_text(
            yaml.dump({
                "default_provider": "anthropic",
                "agent": {"max_steps": 50, "system_prompt": "Custom prompt"},
                "tools": {"enabled": ["read_file", "list_dir"]},
            }),
            encoding="utf-8",
        )

        config = load_config(cli_args=None, work_dir=str(tmp_path))
        assert config.default_provider == "anthropic"
        assert config.model == "claude-sonnet-4-20250514"
        assert config.max_steps == 50
        assert config.system_prompt == "Custom prompt"
        assert config.tools.enabled == ["read_file", "list_dir"]

    # ---------- 用户级 YAML 配置 ----------

    def test_user_yaml_config(self, monkeypatch, tmp_path):
        """用户级 ~/.nexus/nexus.yaml 应覆盖默认值。"""
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "nexus.yaml").write_text(
            yaml.dump({
                "providers": {
                    "openai": {"model": "user-model", "api_key": "sk-user"},
                },
                "agent": {"max_steps": 60},
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "user-model"
        assert config.api_key == "sk-user"
        assert config.max_steps == 60

    # ---------- 配置合并优先级 ----------

    def test_project_overrides_user(self, monkeypatch, tmp_path):
        """项目级 nexus.yaml 应覆盖用户级配置。"""
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "nexus.yaml").write_text(
            yaml.dump({
                "providers": {"openai": {"model": "user-model"}},
                "agent": {"max_steps": 60},
            }),
            encoding="utf-8",
        )

        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "nexus.yaml").write_text(
            yaml.dump({
                "providers": {"openai": {"model": "project-model"}},
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=str(proj))
        assert config.model == "project-model"
        assert config.max_steps == 60  # 用户级字段未被覆盖

    def test_full_priority_chain(self, monkeypatch, tmp_path):
        """验证完整优先级链：CLI > env > project > user > default。"""
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "nexus.yaml").write_text(
            yaml.dump({
                "providers": {"openai": {"model": "user-model"}},
                "agent": {"max_steps": 60},
            }),
            encoding="utf-8",
        )

        proj = tmp_path / "project"
        proj.mkdir()
        (proj / "nexus.yaml").write_text(
            yaml.dump({
                "providers": {"openai": {"model": "project-model"}},
            }),
            encoding="utf-8",
        )

        monkeypatch.setenv("NEXUS_MODEL", "env-model")
        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(
            cli_args={"model": "cli-model"},
            work_dir=str(proj),
        )
        assert config.model == "cli-model"      # CLI 最高
        assert config.max_steps == 60            # 用户级

    # ---------- 文件不存在 ----------

    def test_missing_user_config(self, monkeypatch, tmp_path):
        """用户配置目录不存在时 load_config 不报错。"""
        home = tmp_path / "home_empty"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=None)
        assert config.model == "gpt-4o-mini"

    # ---------- 部分配置 ----------

    def test_partial_yaml_config(self, tmp_path):
        """YAML 只包含部分字段时不影响默认值。"""
        yaml_path = tmp_path / "nexus.yaml"
        yaml_path.write_text(
            yaml.dump({"agent": {"max_steps": 100}}),
            encoding="utf-8",
        )

        config = load_config(cli_args=None, work_dir=str(tmp_path))
        assert config.max_steps == 100
        assert config.model == "gpt-4o-mini"  # 默认值
        assert config.default_provider == "openai"


# ---------------------------------------------------------------------------
# 项目级配置向上查找测试（debug 时 cwd 在子目录也能读到项目根配置）
# ---------------------------------------------------------------------------

class TestFindProjectConfig:
    """测试 _find_project_config 向上查找与 .git 边界。"""

    def test_finds_in_current_dir(self, tmp_path):
        """目标文件就在 start_dir 下。"""
        (tmp_path / "nexus.yaml").write_text("default_provider: openai", encoding="utf-8")
        result = _find_project_config(str(tmp_path), "nexus.yaml")
        assert result == str(tmp_path / "nexus.yaml")

    def test_finds_in_parent(self, tmp_path):
        """start_dir 没有，父目录有 → 返回父目录路径。"""
        (tmp_path / "nexus.yaml").write_text("default_provider: anthropic", encoding="utf-8")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = _find_project_config(str(subdir), "nexus.yaml")
        assert result == str(tmp_path / "nexus.yaml")

    def test_finds_in_deep_subdir(self, tmp_path):
        """深层子目录也能向上找到项目根的配置。"""
        (tmp_path / "nexus.yaml").write_text("default_provider: minimax", encoding="utf-8")
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = _find_project_config(str(deep), "nexus.yaml")
        assert result == str(tmp_path / "nexus.yaml")

    def test_stops_at_git_boundary(self, tmp_path):
        """.git 边界外没有文件 → 返回 None（不越过项目根）。"""
        # tmp_path 作为项目根，放 .git 标记但不放 nexus.yaml
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = _find_project_config(str(subdir), "nexus.yaml")
        assert result is None

    def test_git_boundary_with_config(self, tmp_path):
        """项目根有 .git 也有 nexus.yaml → 能找到。"""
        (tmp_path / ".git").mkdir()
        (tmp_path / "nexus.yaml").write_text("default_provider: openai", encoding="utf-8")
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = _find_project_config(str(subdir), "nexus.yaml")
        assert result == str(tmp_path / "nexus.yaml")

    def test_not_found(self, tmp_path):
        """整个目录链都没有目标文件 → 返回 None。"""
        (tmp_path / ".git").mkdir()  # 项目根但无配置文件
        result = _find_project_config(str(tmp_path), "nexus.yaml")
        assert result is None

    def test_load_config_finds_parent(self, tmp_path, monkeypatch):
        """load_config 从子目录 work_dir 向上读到项目根的 nexus.yaml。"""
        (tmp_path / "nexus.yaml").write_text(
            yaml.dump({
                "default_provider": "anthropic",
                "agent": {"max_steps": 50},
            }),
            encoding="utf-8",
        )
        # 隔离 home 目录，避免 ~/.nexus 干扰
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: empty_home)

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        # work_dir 是子目录，但配置在项目根
        config = load_config(cli_args=None, work_dir=str(subdir))
        assert config.default_provider == "anthropic"
        assert config.max_steps == 50


# ---------------------------------------------------------------------------
# Provider 切换
# ---------------------------------------------------------------------------

class TestProviderSwitch:
    """测试 provider 切换后便捷属性跟随变化。"""

    def test_provider_switch(self):
        """切换 default_provider 后 model/api_key 跟随。"""
        config = NexusConfig(providers=_default_providers())
        # 修改 anthropic 的 api_key
        config.providers["anthropic"].api_key = "sk-ant-123"

        assert config.default_provider == "openai"
        assert config.model == "gpt-4o-mini"
        assert config.api_key is None

        config.default_provider = "anthropic"
        assert config.default_provider == "anthropic"
        assert config.model == "claude-sonnet-4-20250514"
        assert config.api_key == "sk-ant-123"

    def test_provider_switch_to_minimax(self):
        """切换到 minimax 后 base_url 跟随。"""
        config = NexusConfig(providers=_default_providers())
        config.default_provider = "minimax"
        assert config.model == "MiniMax-Text-01"
        assert config.base_url == "https://api.minimaxi.com/anthropic"


# ---------------------------------------------------------------------------
# 向后兼容（旧 JSON 格式）
# ---------------------------------------------------------------------------

class TestJsonBackwardCompat:
    """测试旧 JSON 配置文件仍然可加载。"""

    def test_json_backward_compat(self, tmp_path):
        """旧 .nexus.json 格式仍可加载并正确映射到新结构。"""
        json_path = tmp_path / ".nexus.json"
        json_path.write_text(
            json.dumps({
                "provider": "openai",
                "model": "legacy-model",
                "api_key": "sk-legacy",
                "base_url": "https://legacy.example.com",
                "max_steps": 42,
                "system_prompt": "Legacy prompt",
            }),
            encoding="utf-8",
        )

        config = load_config(cli_args=None, work_dir=str(tmp_path))
        assert config.default_provider == "openai"
        assert config.model == "legacy-model"
        assert config.api_key == "sk-legacy"
        assert config.base_url == "https://legacy.example.com"
        assert config.max_steps == 42
        assert config.system_prompt == "Legacy prompt"

    def test_json_backward_compat_with_user_home(self, monkeypatch, tmp_path):
        """用户级 ~/.nexus/config.json 仍可加载。"""
        home = tmp_path / "home"
        home.mkdir()
        nexus_dir = home / ".nexus"
        nexus_dir.mkdir()
        (nexus_dir / "config.json").write_text(
            json.dumps({
                "provider": "anthropic",
                "model": "claude-model",
                "max_steps": 99,
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: home)

        config = load_config(cli_args=None, work_dir=None)
        assert config.default_provider == "anthropic"
        assert config.model == "claude-model"
        assert config.max_steps == 99


# ---------------------------------------------------------------------------
# Providers 合并测试
# ---------------------------------------------------------------------------

class TestMergeProviders:
    """测试 YAML 中 providers 与默认值合并。"""

    def test_merge_providers(self, tmp_path):
        """YAML providers 部分字段覆盖默认 provider。"""
        yaml_path = tmp_path / "nexus.yaml"
        yaml_path.write_text(
            yaml.dump({
                "providers": {
                    "openai": {
                        "model": "gpt-4-turbo",
                        "api_key": "${MY_KEY:-test-key}",
                    },
                    "custom": {
                        "model": "custom-model",
                        "base_url": "https://custom.api.com",
                    },
                },
            }),
            encoding="utf-8",
        )

        config = load_config(cli_args=None, work_dir=str(tmp_path))
        # openai 被部分覆盖
        assert config.providers["openai"].model == "gpt-4-turbo"
        assert config.providers["openai"].api_key == "test-key"
        assert config.providers["openai"].max_tokens == 4096  # 默认保留
        # custom 是新增 provider
        assert "custom" in config.providers
        assert config.providers["custom"].model == "custom-model"
        assert config.providers["custom"].base_url == "https://custom.api.com"
        # anthropic 保持默认
        assert config.providers["anthropic"].model == "claude-sonnet-4-20250514"

    def test_merge_providers_env_ref(self, monkeypatch, tmp_path):
        """环境变量引用在 provider 中正确解析。"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")

        yaml_path = tmp_path / "nexus.yaml"
        yaml_path.write_text(
            yaml.dump({
                "providers": {
                    "openai": {
                        "api_key": "${OPENAI_API_KEY}",
                        "model": "gpt-4",
                    },
                },
            }),
            encoding="utf-8",
        )

        config = load_config(cli_args=None, work_dir=str(tmp_path))
        assert config.providers["openai"].api_key == "sk-real-key"
        assert config.providers["openai"].model == "gpt-4"


# ---------------------------------------------------------------------------
# Tools 配置测试
# ---------------------------------------------------------------------------

class TestToolsConfig:
    """测试 tools.enabled 配置加载。"""

    def test_tools_config(self, tmp_path):
        """YAML 中 tools.enabled 正确加载。"""
        yaml_path = tmp_path / "nexus.yaml"
        yaml_path.write_text(
            yaml.dump({
                "tools": {"enabled": ["read_file", "write_file"]},
            }),
            encoding="utf-8",
        )

        config = load_config(cli_args=None, work_dir=str(tmp_path))
        assert config.tools.enabled == ["read_file", "write_file"]

    def test_tools_config_default_empty(self):
        """未设置 tools.enabled 时默认为空列表。"""
        config = load_config(cli_args=None, work_dir=None)
        assert config.tools.enabled == []

    def test_tools_config_partial_yaml(self, tmp_path):
        """YAML 中有 tools 但无 enabled 字段时保持默认。"""
        yaml_path = tmp_path / "nexus.yaml"
        yaml_path.write_text(
            yaml.dump({
                "tools": {"other_option": True},
            }),
            encoding="utf-8",
        )

        config = load_config(cli_args=None, work_dir=str(tmp_path))
        assert config.tools.enabled == []


# ---------------------------------------------------------------------------
# init_config 模板生成测试
# ---------------------------------------------------------------------------

class TestInitConfig:
    """测试 --init-config 生成 YAML 模板（generate_config_template）。"""

    def test_init_config(self, tmp_path):
        """generate_config_template 生成完整带注释的模板。"""
        template = generate_config_template()

        # 写入文件验证
        path = str(tmp_path / "nexus.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(template)
        assert os.path.isfile(path)

        # 重新加载验证内容
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert "providers" in data
        assert "openai" in data["providers"]
        assert data["providers"]["openai"]["model"] == "gpt-4o-mini"
        # 模板包含 api_key 占位符（null）
        assert "api_key" in data["providers"]["openai"]
        assert data["providers"]["openai"]["api_key"] is None
        assert data["default_provider"] == "openai"
        assert data["agent"]["max_steps"] == 30
        assert data["agent"]["system_prompt"] == "You are a helpful coding assistant."

    def test_init_config_in_subdir(self, tmp_path):
        """模板可写入任意目录。"""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        path = subdir / "nexus.yaml"
        path.write_text(generate_config_template(), encoding="utf-8")
        assert path.is_file()


# ---------------------------------------------------------------------------
# save_config 测试
# ---------------------------------------------------------------------------

class TestSaveConfig:
    """测试 save_config() 写入 YAML。"""

    def test_save_config(self, tmp_path):
        """save_config 写入 YAML 文件并可回读。"""
        config = NexusConfig(providers=_default_providers())
        config.providers["openai"].model = "custom-model"
        config.providers["openai"].api_key = "sk-test-123"
        config.agent.max_steps = 100
        config.tools.enabled = ["read_file", "search_content"]

        path = str(tmp_path / "saved.yaml")
        saved_path = save_config(config, path)
        assert saved_path == path
        assert os.path.isfile(path)

        # api_key 现在被保留（用户手动管理的配置需要可持久化）
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "api_key" in content
        assert "sk-test-123" in content

        # 重新加载验证
        data = _load_yaml_config(path)
        assert data["providers"]["openai"]["model"] == "custom-model"
        assert data["providers"]["openai"]["api_key"] == "sk-test-123"
        assert data["agent"]["max_steps"] == 100
        assert data["tools"]["enabled"] == ["read_file", "search_content"]

    def test_save_config_default_path(self, monkeypatch, tmp_path):
        """不传 path 时默认保存到 ~/.nexus/nexus.yaml。"""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)

        config = NexusConfig(providers=_default_providers())
        result = save_config(config)  # 使用默认路径
        expected = home / ".nexus" / "nexus.yaml"
        assert os.path.isfile(result)
        assert result == str(expected)

    def test_save_config_roundtrip(self, monkeypatch, tmp_path):
        """save → load 往返测试。"""
        # 隔离环境变量和用户级配置，避免干扰
        for key in ("NEXUS_MODEL", "NEXUS_API_KEY", "NEXUS_BASE_URL",
                     "NEXUS_MAX_STEPS", "NEXUS_PROVIDER", "NEXUS_MAX_TOKENS"):
            monkeypatch.delenv(key, raising=False)
        # 隔离用户 home 目录
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: empty_home)

        original = NexusConfig(providers=_default_providers())
        original.default_provider = "anthropic"
        original.providers["anthropic"].model = "claude-opus"
        original.agent.system_prompt = "Custom"
        original.agent.max_steps = 80
        original.tools.enabled = ["list_dir"]

        path = str(tmp_path / "nexus.yaml")
        save_config(original, path)

        # 用 load_config 重新加载（load_config 读取 <work_dir>/nexus.yaml）
        restored = load_config(cli_args=None, work_dir=str(tmp_path))
        assert restored.default_provider == "anthropic"
        assert restored.providers["anthropic"].model == "claude-opus"
        assert restored.agent.system_prompt == "Custom"
        assert restored.agent.max_steps == 80
        assert restored.tools.enabled == ["list_dir"]


# ---------------------------------------------------------------------------
# 便捷属性测试
# ---------------------------------------------------------------------------

class TestProviderConvenience:
    """测试 NexusConfig 便捷属性指向当前 provider。"""

    def test_provider_convenience(self):
        """model/api_key/base_url/max_tokens 等属性映射到当前 provider。"""
        config = NexusConfig(providers=_default_providers())
        config.providers["openai"].api_key = "sk-test"
        config.providers["openai"].model = "gpt-4"
        config.providers["openai"].base_url = "https://proxy.openai.com"
        config.providers["openai"].max_tokens = 16000

        assert config.model == "gpt-4"
        assert config.api_key == "sk-test"
        assert config.base_url == "https://proxy.openai.com"
        assert config.max_tokens == 16000
        assert config.system_prompt == "You are a helpful coding assistant."
        assert config.max_steps == 30

    def test_provider_convenience_unknown_provider(self):
        """default_provider 不在 providers 中时回退到空 ProviderConfig。"""
        config = NexusConfig(providers=_default_providers())
        config.default_provider = "nonexistent"
        assert config.model == ""
        assert config.api_key is None
        assert config.base_url is None


# ---------------------------------------------------------------------------
# 内置 Provider 测试
# ---------------------------------------------------------------------------

class TestDefaultProvidersBuiltin:
    """测试内置 provider 配置。"""

    def test_default_providers_builtin(self):
        """内置 openai/anthropic/minimax 三个 provider。"""
        providers = _default_providers()

        assert "openai" in providers
        assert "anthropic" in providers
        assert "minimax" in providers

        # openai
        openai = providers["openai"]
        assert openai.model == "gpt-4o-mini"
        assert openai.max_tokens == 4096
        assert openai.base_url is None

        # anthropic
        anthropic = providers["anthropic"]
        assert anthropic.model == "claude-sonnet-4-20250514"
        assert anthropic.max_tokens == 4096

        # minimax
        minimax = providers["minimax"]
        assert minimax.model == "MiniMax-Text-01"
        assert minimax.base_url == "https://api.minimaxi.com/anthropic"
