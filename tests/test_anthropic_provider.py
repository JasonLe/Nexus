"""AnthropicLLM._split_system_messages 消息格式转换测试。

测试 Runtime 内部使用的 OpenAI 格式消息（tool_calls 顶层字段、role="tool"）
正确转换为 Anthropic API 要求的格式（tool_use block、tool_result block）。
"""

import json

from nexus.llm.providers.anthropic import AnthropicLLM


class TestSplitSystemMessages:
    """测试 _split_system_messages 的格式转换。"""

    def test_system_prompt_extraction(self):
        """system 消息提取为独立 system_prompt 参数。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system_prompt, anthropic_messages = AnthropicLLM._split_system_messages(messages)
        assert system_prompt == "You are helpful."
        assert len(anthropic_messages) == 1
        assert anthropic_messages[0]["role"] == "user"

    def test_multiple_system_prompts_joined(self):
        """多个 system 消息用双换行连接。"""
        messages = [
            {"role": "system", "content": "Rule 1."},
            {"role": "system", "content": "Rule 2."},
            {"role": "user", "content": "Hi"},
        ]
        system_prompt, _ = AnthropicLLM._split_system_messages(messages)
        assert system_prompt == "Rule 1.\n\nRule 2."

    def test_plain_user_message(self):
        """普通 user 消息保持 role + content。"""
        messages = [{"role": "user", "content": "Hello"}]
        _, anthropic_messages = AnthropicLLM._split_system_messages(messages)
        assert anthropic_messages[0]["role"] == "user"
        assert anthropic_messages[0]["content"] == "Hello"

    def test_assistant_with_tool_calls(self):
        """assistant 的 tool_calls 转为 tool_use block。"""
        messages = [
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {
                        "id": "toolu_123",
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "arguments": json.dumps({"path": "."}),
                        },
                    }
                ],
            },
        ]
        _, anthropic_messages = AnthropicLLM._split_system_messages(messages)

        # user 消息
        assert anthropic_messages[0]["role"] == "user"
        assert anthropic_messages[0]["content"] == "list files"

        # assistant 消息 —— content 应为 list，包含 text + tool_use
        assistant_msg = anthropic_messages[1]
        assert assistant_msg["role"] == "assistant"
        assert isinstance(assistant_msg["content"], list)
        assert assistant_msg["content"][0]["type"] == "text"
        assert assistant_msg["content"][0]["text"] == "Let me check."
        assert assistant_msg["content"][1]["type"] == "tool_use"
        assert assistant_msg["content"][1]["id"] == "toolu_123"
        assert assistant_msg["content"][1]["name"] == "list_dir"
        assert assistant_msg["content"][1]["input"] == {"path": "."}

    def test_assistant_tool_calls_with_dict_arguments(self):
        """arguments 为 dict 时直接用作 input。"""
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "toolu_456",
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "/tmp/test.txt"},
                        },
                    }
                ],
            }
        ]
        _, anthropic_messages = AnthropicLLM._split_system_messages(messages)
        tool_use_block = anthropic_messages[0]["content"][0]
        assert tool_use_block["type"] == "tool_use"
        assert tool_use_block["input"] == {"path": "/tmp/test.txt"}

    def test_tool_result_conversion(self):
        """role='tool' 转为 role='user' + tool_result block。"""
        messages = [
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_123",
                        "function": {
                            "name": "list_dir",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_123",
                "content": "file1.txt\nfile2.txt",
            },
        ]
        _, anthropic_messages = AnthropicLLM._split_system_messages(messages)

        # tool result 应转为 user 消息
        tool_msg = anthropic_messages[2]
        assert tool_msg["role"] == "user"
        assert isinstance(tool_msg["content"], list)
        assert tool_msg["content"][0]["type"] == "tool_result"
        assert tool_msg["content"][0]["tool_use_id"] == "toolu_123"
        assert tool_msg["content"][0]["content"] == "file1.txt\nfile2.txt"

    def test_consecutive_tool_results_merged(self):
        """连续多个 tool result 合并到同一个 user 消息。"""
        messages = [
            {"role": "user", "content": "check two dirs"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "function": {"name": "list_dir", "arguments": "{}"},
                    },
                    {
                        "id": "toolu_2",
                        "function": {"name": "list_dir", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "dir1 files"},
            {"role": "tool", "tool_call_id": "toolu_2", "content": "dir2 files"},
        ]
        _, anthropic_messages = AnthropicLLM._split_system_messages(messages)

        # 两个 tool result 应合并到一条 user 消息
        tool_msg = anthropic_messages[2]
        assert tool_msg["role"] == "user"
        assert len(tool_msg["content"]) == 2
        assert tool_msg["content"][0]["tool_use_id"] == "toolu_1"
        assert tool_msg["content"][1]["tool_use_id"] == "toolu_2"

    def test_full_tool_call_cycle(self):
        """完整工具调用循环：user → assistant(tool_use) → tool_result → assistant。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "list files"},
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {
                        "id": "toolu_abc",
                        "function": {
                            "name": "list_dir",
                            "arguments": json.dumps({"path": "."}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_abc",
                "content": "file1.txt",
            },
            {"role": "assistant", "content": "Found 1 file: file1.txt"},
        ]
        system_prompt, anthropic_messages = AnthropicLLM._split_system_messages(messages)

        assert system_prompt == "You are helpful."
        # 应有 4 条消息：user, assistant(tool_use), user(tool_result), assistant(text)
        assert len(anthropic_messages) == 4

        assert anthropic_messages[0]["role"] == "user"
        assert anthropic_messages[0]["content"] == "list files"

        assert anthropic_messages[1]["role"] == "assistant"
        assert isinstance(anthropic_messages[1]["content"], list)

        assert anthropic_messages[2]["role"] == "user"
        assert anthropic_messages[2]["content"][0]["type"] == "tool_result"
        assert anthropic_messages[2]["content"][0]["tool_use_id"] == "toolu_abc"

        assert anthropic_messages[3]["role"] == "assistant"

    def test_empty_messages_fallback(self):
        """空消息列表兜底为 Hello。"""
        _, anthropic_messages = AnthropicLLM._split_system_messages([])
        assert anthropic_messages[0]["role"] == "user"
        assert anthropic_messages[0]["content"] == "Hello"

    def test_invalid_json_arguments_handled(self):
        """arguments 为非法 JSON 时不崩溃，降级为空 dict。"""
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "toolu_x",
                        "function": {
                            "name": "list_dir",
                            "arguments": "{invalid json",
                        },
                    }
                ],
            }
        ]
        _, anthropic_messages = AnthropicLLM._split_system_messages(messages)
        tool_use_block = anthropic_messages[0]["content"][0]
        assert tool_use_block["input"] == {}
