r"""Nexus 基础 Agent 示例 —— 最小可运行的 "Hello World"

本示例展示 Nexus 框架的核心概念：
1. 如何创建 LLM 实例（示例使用 MockLLM，无需真实 API key）
2. 如何创建 Agent 并注册工具
3. 如何执行任务并查看结果

运行方式：
    cd d:\Nexus && python examples\basic_agent.py

注意：本示例使用 MockLLM 模拟 LLM 调用，因此无需设置任何 API key。
     真实使用时，将 MockLLM 替换为 OpenAILLM 即可（参见注释说明）。
"""

import asyncio
from typing import Any, AsyncIterator

from nexus.core.agent.agent import Agent
from nexus.llm.base import (
    BaseLLM,
    LLMResponse,
    LLMChunk,
    UsageStats,
)
from nexus.tools.builtins import EchoTool


# ---------------------------------------------------------------------------
# MockLLM —— 用于演示的模拟 LLM，无需真实 API key
# ---------------------------------------------------------------------------
# 真实使用时，替换为：
#   from nexus.llm.providers.openai import OpenAIBackend
#   llm = OpenAIBackend(model="gpt-4o-mini", api_key="sk-xxx")
# ---------------------------------------------------------------------------

class MockLLM(BaseLLM):
    """模拟 LLM —— 总是返回固定文本 "Hello from Nexus!"。

    继承自 BaseLLM，实现了 chat() 和 stream_chat() 两个抽象方法。
    不发送任何网络请求，无需 API key，适合单元测试和快速验证框架流程。
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """非流式对话 —— 返回固定的问候消息。

        真实 LLM（如 OpenAI）会将 messages 发送到 API 并获取智能回复。
        此处我们直接返回一个硬编码的 LLMResponse，模拟"LLM 做出了回复"的语义。
        """
        return LLMResponse(
            content="Hello from Nexus! 这是一个来自 MockLLM 的问候。",
            usage=UsageStats(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="mock-llm-v1",
            finish_reason="stop",
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """流式对话 —— 逐词产出文本（模拟打字效果）。

        真实 LLM 会通过 SSE 逐步返回 token，由 SDK 封装为 yield chunk。
        此处我们手动拆分为两个 chunk，展示流式处理的格式。
        """
        yield LLMChunk(delta_content="Hello ")
        yield LLMChunk(delta_content="from Nexus!")


# ---------------------------------------------------------------------------
# 主函数 —— 演示 Agent 的创建、工具注册和任务执行
# ---------------------------------------------------------------------------

async def main() -> None:
    """演示 Nexus Agent 的最小可运行流程。"""

    # 1. 创建 LLM 实例
    #    真实用法：llm = OpenAILLM(model="gpt-4o-mini")
    #    这里用 MockLLM 代替，无需 API key
    print("[1/5] 创建 MockLLM（模拟 LLM）...")
    llm = MockLLM()

    # 2. 创建 Agent
    #    - llm：负责推理的 LLM 实例
    #    - system_prompt：注入为对话的第一条 system 消息，定义 Agent 的角色和行为
    #    - max_steps：最大执行步数，防止 LLM 无限循环调用工具
    #    - 若不指定 policy，默认使用 ReActPolicy（交替 LLM 思考和工具调用）
    print("[2/5] 创建 Agent...")
    agent = Agent(
        llm=llm,
        system_prompt="你是一个友好的助手，总是用中文回复。",
        max_steps=10,
        name="hello-world-agent",
    )

    # 3. 注册工具
    #    EchoTool 是框架内置的测试工具，原样返回输入消息。
    #    Agent 会将工具的 name/description/schema 暴露给 LLM，
    #    LLM 通过 function calling 机制决定何时调用工具。
    print("[3/5] 注册 EchoTool 工具...")
    agent.register_tool(EchoTool())
    print(f"      已注册工具：echo（原样返回输入消息）")

    # 4. 执行任务
    #    Agent.run() 是主入口方法，内部委托给 Runtime 完成完整的调度循环：
    #    Policy.next_action → Runtime 执行 Action → 更新 State → 循环...
    #    返回的 AgentState 包含完整的对话历史、执行步骤和中间结果。
    print("[4/5] 执行任务：'Say hello'...")
    state = await agent.run(task="Say hello")

    # 5. 查看结果
    #    - state.messages：完整的对话历史（system + user + assistant 消息）
    #    - state.current_step：实际执行的总步数
    #    - state.intermediate_results：中间计算结果和最终结果
    print("[5/5] 查看执行结果：")
    print(f"      执行步数：{state.current_step}")
    print(f"      消息数量：{len(state.messages)}")
    print(f"      对话历史：")
    for i, msg in enumerate(state.messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "(无内容)")
        # 截断过长的内容便于阅读
        content_preview = content[:100] + "..." if len(str(content)) > 100 else content
        print(f"        [{i}] {role}: {content_preview}")

    # 打印最终结果
    final = state.intermediate_results.get("final_result")
    finish_msg = state.intermediate_results.get("finish_message")
    print(f"\n      最终状态：finish_message={finish_msg}")
    print(f"      最终结果：{final}")


if __name__ == "__main__":
    # asyncio.run() 创建事件循环并运行 main() 协程
    # 这是 Python 官方推荐的异步程序入口
    asyncio.run(main())
