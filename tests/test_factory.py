"""测试 nexus.core.factory 工厂模块。

覆盖范围
--------
- create_llm: 三个 provider 分支 (openai/anthropic/minimax) —— 用 mock 验证
  创建的 LLM 类型与传入参数正确，并覆盖默认 model / base_url 回退逻辑
- register_tools: 工具过滤 —— enabled=[] 注册全部，enabled=["read_file","shell"]
  只注册两个
- create_agent: 端到端组合验证 LLM 注入与工具注册
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nexus.cli.config import NexusConfig, ProviderConfig, ToolsConfig
from nexus.core.factory import create_agent, create_llm, register_tools


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_config(
    provider: str,
    model: str = "",
    base_url: str | None = None,
    context_window_tokens: int = 128000,
    enabled_tools: list[str] | None = None,
    work_dir: str = "",
) -> NexusConfig:
    """构造测试用 NexusConfig。"""
    pc = ProviderConfig(
        api_key="test-key",
        model=model,
        base_url=base_url,
        context_window_tokens=context_window_tokens,
    )
    tools = ToolsConfig(enabled=enabled_tools if enabled_tools is not None else [])
    return NexusConfig(
        providers={provider: pc},
        default_provider=provider,
        tools=tools,
        work_dir=work_dir,
    )


# ---------------------------------------------------------------------------
# create_llm —— provider 分支测试
# ---------------------------------------------------------------------------


class TestCreateLlm:
    """测试 create_llm 的三个 provider 分支。"""

    @patch("nexus.llm.providers.openai.OpenAILLM")
    def test_openai_provider(self, mock_openai_cls):
        """openai 分支应创建 OpenAILLM 实例并传入正确参数。"""
        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance

        config = _make_config("openai", model="gpt-4o", base_url="https://api.example.com")
        llm = create_llm(config)

        assert llm is mock_instance
        mock_openai_cls.assert_called_once()
        _, kwargs = mock_openai_cls.call_args
        assert kwargs["api_key"] == "test-key"
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["base_url"] == "https://api.example.com"
        assert kwargs["context_window_tokens"] == 128000

    @patch("nexus.llm.providers.openai.OpenAILLM")
    def test_openai_default_model(self, mock_openai_cls):
        """openai 分支 model 为空时应回退到 gpt-4o-mini。"""
        mock_openai_cls.return_value = MagicMock()
        config = _make_config("openai", model="")
        create_llm(config)

        _, kwargs = mock_openai_cls.call_args
        assert kwargs["model"] == "gpt-4o-mini"

    @patch("nexus.llm.providers.openai.OpenAILLM")
    def test_unknown_provider_falls_back_to_openai(self, mock_openai_cls):
        """未知 provider 应走 openai 分支（else 默认）。"""
        mock_openai_cls.return_value = MagicMock()
        config = _make_config("some-unknown-provider", model="custom-model")
        create_llm(config)

        mock_openai_cls.assert_called_once()
        _, kwargs = mock_openai_cls.call_args
        assert kwargs["model"] == "custom-model"

    @patch("nexus.llm.providers.anthropic.AnthropicLLM")
    def test_anthropic_provider(self, mock_anthropic_cls):
        """anthropic 分支应创建 AnthropicLLM 实例并传入正确参数。"""
        mock_instance = MagicMock()
        mock_anthropic_cls.return_value = mock_instance

        config = _make_config(
            "anthropic",
            model="claude-sonnet-4-20250514",
            base_url="https://api.anthropic.com",
            context_window_tokens=200000,
        )
        llm = create_llm(config)

        assert llm is mock_instance
        mock_anthropic_cls.assert_called_once()
        _, kwargs = mock_anthropic_cls.call_args
        assert kwargs["api_key"] == "test-key"
        assert kwargs["model"] == "claude-sonnet-4-20250514"
        assert kwargs["base_url"] == "https://api.anthropic.com"
        assert kwargs["context_window_tokens"] == 200000

    @patch("nexus.llm.providers.anthropic.AnthropicLLM")
    def test_anthropic_default_model_no_base_url(self, mock_anthropic_cls):
        """anthropic 分支 model 为空时回退默认 model，base_url 为空时不传 base_url。"""
        mock_anthropic_cls.return_value = MagicMock()
        config = _make_config("anthropic", model="", base_url=None)
        create_llm(config)

        _, kwargs = mock_anthropic_cls.call_args
        assert kwargs["model"] == "claude-sonnet-4-20250514"
        # base_url 为 None 时不应出现在 kwargs 中
        assert "base_url" not in kwargs

    @patch("nexus.llm.providers.minimax.MiniMaxAnthropicLLM")
    def test_minimax_provider(self, mock_minimax_cls):
        """minimax 分支应创建 MiniMaxAnthropicLLM 实例并传入正确参数。"""
        mock_instance = MagicMock()
        mock_minimax_cls.return_value = mock_instance

        config = _make_config(
            "minimax",
            model="MiniMax-Text-01",
            base_url="https://api.minimaxi.com/anthropic",
            context_window_tokens=245760,
        )
        llm = create_llm(config)

        assert llm is mock_instance
        mock_minimax_cls.assert_called_once()
        _, kwargs = mock_minimax_cls.call_args
        assert kwargs["api_key"] == "test-key"
        assert kwargs["model"] == "MiniMax-Text-01"
        assert kwargs["base_url"] == "https://api.minimaxi.com/anthropic"
        assert kwargs["context_window_tokens"] == 245760

    @patch("nexus.llm.providers.minimax.MiniMaxAnthropicLLM")
    def test_minimax_default_model_and_base_url(self, mock_minimax_cls):
        """minimax 分支 model/base_url 为空时回退到默认值。"""
        mock_minimax_cls.return_value = MagicMock()
        config = _make_config("minimax", model="", base_url=None)
        create_llm(config)

        _, kwargs = mock_minimax_cls.call_args
        assert kwargs["model"] == "MiniMax-Text-01"
        assert kwargs["base_url"] == "https://api.minimaxi.com/anthropic"

    @patch("nexus.llm.providers.openai.OpenAILLM")
    def test_provider_case_insensitive(self, mock_openai_cls):
        """provider 名称大小写不敏感（.lower() 处理）。"""
        mock_openai_cls.return_value = MagicMock()
        config = _make_config("OpenAI", model="gpt-4o")
        create_llm(config)

        mock_openai_cls.assert_called_once()


# ---------------------------------------------------------------------------
# register_tools —— 工具过滤测试
# ---------------------------------------------------------------------------


class TestRegisterTools:
    """测试 register_tools 的工具过滤逻辑。"""

    def test_register_all_when_enabled_empty(self, tmp_path):
        """enabled=[] 时应注册全部 5 个内置工具。"""
        mock_llm = MagicMock()
        from nexus.core.agent.agent import Agent

        agent = Agent(llm=mock_llm)
        config = _make_config("openai", work_dir=str(tmp_path), enabled_tools=[])

        register_tools(agent, config)

        tool_names = {t.name for t in agent.tool_registry.list()}
        assert tool_names == {
            "read_file",
            "write_file",
            "list_dir",
            "search_content",
            "shell",
        }
        assert len(agent.tool_registry) == 5

    def test_register_all_when_enabled_none(self, tmp_path):
        """enabled 为 None（未设置）时也应注册全部工具。

        通过直接构造 ToolsConfig(enabled=[]) 模拟空列表场景，
        与 enabled 为 falsy 的行为一致。
        """
        mock_llm = MagicMock()
        from nexus.core.agent.agent import Agent

        agent = Agent(llm=mock_llm)
        config = _make_config("openai", work_dir=str(tmp_path), enabled_tools=[])

        register_tools(agent, config)

        assert len(agent.tool_registry) == 5

    def test_register_subset_when_enabled_specified(self, tmp_path):
        """enabled=["read_file","shell"] 时只注册这两个工具。"""
        mock_llm = MagicMock()
        from nexus.core.agent.agent import Agent

        agent = Agent(llm=mock_llm)
        config = _make_config(
            "openai",
            work_dir=str(tmp_path),
            enabled_tools=["read_file", "shell"],
        )

        register_tools(agent, config)

        tool_names = {t.name for t in agent.tool_registry.list()}
        assert tool_names == {"read_file", "shell"}
        assert len(agent.tool_registry) == 2
        # 未启用的工具不应在注册表中
        assert "write_file" not in agent.tool_registry
        assert "list_dir" not in agent.tool_registry
        assert "search_content" not in agent.tool_registry

    def test_register_single_tool(self, tmp_path):
        """enabled 只含一个工具时只注册该工具。"""
        mock_llm = MagicMock()
        from nexus.core.agent.agent import Agent

        agent = Agent(llm=mock_llm)
        config = _make_config(
            "openai",
            work_dir=str(tmp_path),
            enabled_tools=["list_dir"],
        )

        register_tools(agent, config)

        assert len(agent.tool_registry) == 1
        assert "list_dir" in agent.tool_registry

    def test_register_unknown_tool_name_ignored(self, tmp_path):
        """enabled 含未知工具名时应忽略未知项，仅注册存在的工具。"""
        mock_llm = MagicMock()
        from nexus.core.agent.agent import Agent

        agent = Agent(llm=mock_llm)
        config = _make_config(
            "openai",
            work_dir=str(tmp_path),
            enabled_tools=["read_file", "nonexistent_tool"],
        )

        register_tools(agent, config)

        assert len(agent.tool_registry) == 1
        assert "read_file" in agent.tool_registry


# ---------------------------------------------------------------------------
# create_agent —— 端到端组合测试
# ---------------------------------------------------------------------------


class TestCreateAgent:
    """测试 create_agent 端到端组装。"""

    @patch("nexus.llm.providers.openai.OpenAILLM")
    def test_create_agent_full_assembly(self, mock_openai_cls, tmp_path):
        """create_agent 应完成 LLM 创建 + Agent 构造 + 工具注册全流程。"""
        mock_llm_instance = MagicMock()
        mock_openai_cls.return_value = mock_llm_instance

        config = _make_config(
            "openai",
            model="gpt-4o",
            work_dir=str(tmp_path),
            enabled_tools=["read_file", "shell"],
        )
        config.agent.system_prompt = "You are a test assistant."
        config.agent.max_steps = 10
        config.stream = False

        agent = create_agent(config)

        # LLM 应被注入 Agent
        assert agent.llm is mock_llm_instance
        # system_prompt / max_steps / stream 应正确传递
        assert agent.system_prompt == "You are a test assistant."
        assert agent.max_steps == 10
        # 工具应按 enabled 过滤注册
        tool_names = {t.name for t in agent.tool_registry.list()}
        assert tool_names == {"read_file", "shell"}

    @patch("nexus.llm.providers.openai.OpenAILLM")
    def test_create_agent_registers_all_by_default(self, mock_openai_cls, tmp_path):
        """create_agent 默认（enabled=[]）注册全部工具。"""
        mock_openai_cls.return_value = MagicMock()

        config = _make_config("openai", work_dir=str(tmp_path), enabled_tools=[])

        agent = create_agent(config)

        assert len(agent.tool_registry) == 5
