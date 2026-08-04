"""测试 MCP 配置层：MCPServerConfig 与 mcp_servers 的加载/容错/保存/模板。

覆盖范围
--------
- MCPServerConfig.transport：有 url → "http"，否则 "stdio"
- load_config() 解析 mcp_servers 节（字段正确、env ${VAR} 引用解析）
- 非法配置容错：缺 command 且缺 url / command 类型错误 → 跳过，合法项保留
- save_config() → load_config() 往返一致；无 mcp_servers 时不写该节
- generate_config_template() 包含 mcp_servers

说明：load_config 会合并用户级 ~/.nexus/nexus.yaml，为隔离本机环境，
测试统一 monkeypatch ``Path.home()`` 到 tmp_path 下的空目录，并清理
NEXUS_* 环境变量（参考 test_cli_config.py 的既有模式）。
"""

from pathlib import Path

import pytest
import yaml

from nexus.cli.config import (
    MCPServerConfig,
    NexusConfig,
    _load_yaml_config,
    generate_config_template,
    load_config,
    save_config,
)


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """隔离用户级 ~/.nexus、NEXUS_* 环境变量，并阻止向上查找外部配置文件。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for key in (
        "NEXUS_MODEL", "NEXUS_API_KEY", "NEXUS_BASE_URL",
        "NEXUS_MAX_STEPS", "NEXUS_PROVIDER", "NEXUS_MAX_TOKENS",
    ):
        monkeypatch.delenv(key, raising=False)
    # .git 标记项目边界：即使 tmp_path 下没有 nexus.yaml，
    # 也不会向上合并到用户目录/项目根的真实配置
    (tmp_path / ".git").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# MCPServerConfig.transport
# ---------------------------------------------------------------------------


class TestTransport:
    """测试 MCPServerConfig.transport 判定。"""

    def test_http_when_url_set(self):
        """配置了 url 时 transport 为 http。"""
        cfg = MCPServerConfig(url="http://localhost:3000/mcp")
        assert cfg.transport == "http"

    def test_stdio_by_default(self):
        """无 url 时 transport 为 stdio（即使没有 command）。"""
        assert MCPServerConfig(command="npx").transport == "stdio"
        assert MCPServerConfig().transport == "stdio"


# ---------------------------------------------------------------------------
# load_config 解析 mcp_servers
# ---------------------------------------------------------------------------


class TestLoadMcpServers:
    """测试 load_config 对 mcp_servers 节的解析。"""

    def test_load_stdio_and_http(self, isolated_env, tmp_path):
        """stdio 与 http 两种形态的 server 均正确解析。"""
        (tmp_path / "nexus.yaml").write_text(
            yaml.dump({
                "mcp_servers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                        "env": {"API_KEY": "sk-test"},
                        "enabled": True,
                    },
                    "remote": {
                        "url": "http://localhost:3000/mcp",
                        "enabled": False,
                    },
                },
            }),
            encoding="utf-8",
        )

        config = load_config(work_dir=str(tmp_path))
        servers = config.mcp_servers
        assert set(servers) == {"filesystem", "remote"}

        fs = servers["filesystem"]
        assert fs.command == "npx"
        assert fs.args == ["-y", "@modelcontextprotocol/server-filesystem", "."]
        assert fs.env == {"API_KEY": "sk-test"}
        assert fs.enabled is True
        assert fs.transport == "stdio"

        remote = servers["remote"]
        assert remote.url == "http://localhost:3000/mcp"
        assert remote.command is None
        assert remote.enabled is False
        assert remote.transport == "http"

    def test_env_var_ref_resolved(self, isolated_env, tmp_path, monkeypatch):
        """env 值中的 ${VAR} 与 ${VAR:-default} 引用被解析。"""
        monkeypatch.setenv("TEST_MCP_TOKEN", "real-token")
        (tmp_path / "nexus.yaml").write_text(
            yaml.dump({
                "mcp_servers": {
                    "git": {
                        "command": "npx",
                        "env": {
                            "GITHUB_TOKEN": "${TEST_MCP_TOKEN}",
                            "FALLBACK": "${MISSING_VAR:-default-key}",
                        },
                    },
                },
            }),
            encoding="utf-8",
        )

        config = load_config(work_dir=str(tmp_path))
        env = config.mcp_servers["git"].env
        assert env["GITHUB_TOKEN"] == "real-token"
        assert env["FALLBACK"] == "default-key"

    def test_empty_section(self, isolated_env, tmp_path):
        """mcp_servers 为空 dict 时加载为空。"""
        (tmp_path / "nexus.yaml").write_text(
            yaml.dump({"mcp_servers": {}}), encoding="utf-8"
        )
        config = load_config(work_dir=str(tmp_path))
        assert config.mcp_servers == {}


# ---------------------------------------------------------------------------
# 非法配置容错
# ---------------------------------------------------------------------------


class TestInvalidMcpConfig:
    """测试非法 mcp_servers 条目被跳过、合法项保留。"""

    def test_skip_invalid_keep_valid(self, isolated_env, tmp_path):
        """缺 command 且缺 url、command 类型错误 → 跳过；合法项保留。"""
        (tmp_path / "nexus.yaml").write_text(
            yaml.dump({
                "mcp_servers": {
                    "no_cmd_url": {"enabled": True},   # 缺 command 且缺 url
                    "bad_command": {"command": 123},   # command 类型错误
                    "good": {"command": "npx", "args": ["-y", "pkg"]},
                },
            }),
            encoding="utf-8",
        )

        config = load_config(work_dir=str(tmp_path))
        assert set(config.mcp_servers) == {"good"}

    def test_bad_args_skipped(self, isolated_env, tmp_path):
        """args 非 list 时跳过该条目。"""
        (tmp_path / "nexus.yaml").write_text(
            yaml.dump({
                "mcp_servers": {
                    "bad_args": {"command": "npx", "args": "not-a-list"},
                },
            }),
            encoding="utf-8",
        )
        config = load_config(work_dir=str(tmp_path))
        assert config.mcp_servers == {}

    def test_non_dict_entry_skipped(self, isolated_env, tmp_path):
        """条目非 dict（如字符串）时跳过。"""
        (tmp_path / "nexus.yaml").write_text(
            yaml.dump({
                "mcp_servers": {
                    "scalar": "just-a-string",
                    "good": {"command": "npx"},
                },
            }),
            encoding="utf-8",
        )
        config = load_config(work_dir=str(tmp_path))
        assert set(config.mcp_servers) == {"good"}


# ---------------------------------------------------------------------------
# save_config / load_config 往返
# ---------------------------------------------------------------------------


class TestSaveMcpConfig:
    """测试 save_config 对 mcp_servers 的持久化。"""

    def test_save_load_roundtrip(self, isolated_env, tmp_path):
        """save_config 写入 mcp_servers 后 load_config 往返一致。"""
        config = NexusConfig()
        config.mcp_servers["filesystem"] = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "."],
            env={"API_KEY": "sk-123"},
            enabled=True,
        )
        config.mcp_servers["remote"] = MCPServerConfig(
            url="http://localhost:3000/mcp",
            enabled=False,
        )

        # 保存到 work_dir 下的 nexus.yaml，使 load_config 能读到
        path = str(tmp_path / "nexus.yaml")
        save_config(config, path)

        # 直接读 YAML 验证结构
        raw = _load_yaml_config(path)
        assert set(raw["mcp_servers"]) == {"filesystem", "remote"}
        assert raw["mcp_servers"]["filesystem"]["command"] == "npx"
        assert raw["mcp_servers"]["filesystem"]["env"] == {"API_KEY": "sk-123"}
        assert raw["mcp_servers"]["remote"]["url"] == "http://localhost:3000/mcp"
        assert raw["mcp_servers"]["remote"]["enabled"] is False

        # load_config 往返
        restored = load_config(work_dir=str(tmp_path))
        assert set(restored.mcp_servers) == {"filesystem", "remote"}
        fs = restored.mcp_servers["filesystem"]
        assert fs.command == "npx"
        assert fs.args == ["-y", "@modelcontextprotocol/server-filesystem", "."]
        assert fs.env == {"API_KEY": "sk-123"}
        assert fs.enabled is True
        remote = restored.mcp_servers["remote"]
        assert remote.url == "http://localhost:3000/mcp"
        assert remote.enabled is False

    def test_save_without_mcp_servers(self, isolated_env, tmp_path):
        """无 mcp_servers 时保存的文件不写 mcp_servers 节。"""
        config = NexusConfig()
        path = str(tmp_path / "nexus.yaml")
        save_config(config, path)
        raw = _load_yaml_config(path)
        assert "mcp_servers" not in raw


# ---------------------------------------------------------------------------
# 配置模板
# ---------------------------------------------------------------------------


class TestMcpTemplate:
    """测试 generate_config_template 包含 mcp_servers。"""

    def test_template_contains_mcp_servers(self):
        """模板包含 mcp_servers 注释节与示例。"""
        template = generate_config_template()
        assert "mcp_servers" in template
        assert "command: npx" in template
        assert "enabled: false" in template
