"""Token 计数与 Context Window 截断测试。"""

from nexus.llm.token_counter import (
    estimate_message_tokens,
    truncate_messages_to_fit,
)


class TestEstimateMessageTokens:
    """estimate_message_tokens 测试。"""

    def test_empty_messages(self):
        """空消息列表 token 数为基础 overhead。"""
        tokens = estimate_message_tokens([])
        assert tokens == 3  # 基础 overhead

    def test_single_user_message(self):
        """单条 user 消息。"""
        tokens = estimate_message_tokens(
            [{"role": "user", "content": "Hello world"}]
        )
        # 3 base + 3 msg overhead + content tokens
        assert tokens > 6

    def test_chinese_content_estimation(self):
        """中文内容字符近似估算（tiktoken 不可用时）。"""
        tokens = estimate_message_tokens(
            [{"role": "user", "content": "你好世界"}]
        )
        # 4 中文字符 / 1.5 ≈ 2.67 → 3 tokens + overhead
        assert tokens > 6

    def test_message_with_tool_calls(self):
        """带 tool_calls 的消息。"""
        tokens = estimate_message_tokens([
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "search",
                            "arguments": '{"q": "test"}',
                        }
                    }
                ],
            }
        ])
        assert tokens > 10

    def test_content_as_list(self):
        """content 为 list（Anthropic block 格式）。"""
        tokens = estimate_message_tokens([
            {"role": "user", "content": [{"type": "text", "text": "Hi"}]}
        ])
        assert tokens > 6


class TestTruncateMessagesToFit:
    """truncate_messages_to_fit 测试。"""

    def test_no_truncation_when_within_limit(self):
        """消息未超限时原样返回。"""
        messages = [{"role": "user", "content": "short"}]
        result = truncate_messages_to_fit(messages, max_tokens=1000)
        assert result == messages

    def test_truncation_removes_oldest_non_system(self):
        """超限时删除最早的非 system 消息。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "old message " * 50},
            {"role": "assistant", "content": "old reply " * 50},
            {"role": "user", "content": "recent question"},
        ]
        result = truncate_messages_to_fit(messages, max_tokens=50, keep_recent=1)

        # system 应保留
        assert result[0]["role"] == "system"
        # 最近的 user 消息应保留
        assert result[-1]["content"] == "recent question"
        # 结果应比原始短
        assert len(result) < len(messages)

    def test_keep_system_always(self):
        """system 消息始终保留。"""
        messages = [
            {"role": "system", "content": "System prompt " * 20},
            {"role": "user", "content": "q" * 200},
            {"role": "assistant", "content": "a" * 200},
            {"role": "user", "content": "recent"},
        ]
        result = truncate_messages_to_fit(messages, max_tokens=50, keep_system=True)
        assert any(m["role"] == "system" for m in result)

    def test_keep_recent_preserves_last_n(self):
        """keep_recent 保留最后 N 条消息。"""
        messages = [
            {"role": "user", "content": "old " * 30},
            {"role": "assistant", "content": "old reply " * 30},
            {"role": "user", "content": "keep1"},
            {"role": "assistant", "content": "keep2"},
        ]
        result = truncate_messages_to_fit(messages, max_tokens=30, keep_recent=2)
        # 最后 2 条应保留
        assert result[-1]["content"] == "keep2"
        assert result[-2]["content"] == "keep1"

    def test_max_tokens_zero_no_truncation(self):
        """max_tokens=0 时不截断。"""
        messages = [{"role": "user", "content": "x" * 1000}]
        result = truncate_messages_to_fit(messages, max_tokens=0)
        assert result == messages
