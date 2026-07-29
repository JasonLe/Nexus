r"""Nexus 自定义执行策略示例 —— 展示如何实现自定义 ExecutionPolicy

本示例实现一个 RolodexPolicy（"名片盒策略"），展示 Policy 的核心思想：
- Policy 是"决策者"，不直接调用 LLM 或 Tool，只返回 Action 指令
- Runtime 是"执行者"，根据 Action 类型执行具体操作（LLM / Tool / Finish）
- 两者通过 ExecutionContext 协作，形成完整的调度循环

RolodexPolicy 的固定步骤序列（完全不依赖 LLM 的 tool_calls 响应）：
  步骤 0：调用 LLM 进行初步推理
  步骤 1：调用 Echo 工具（无论 LLM 说什么，都执行）
  步骤 2：再次调用 LLM，让它根据工具结果给出最终答案
  步骤 3+：结束执行

运行方式：
    cd d:\Nexus && python examples\custom_policy.py

与默认 ReActPolicy 的核心区别：
- ReActPolicy：根据 LLM 是否返回 tool_calls 动态决定下一步
- RolodexPolicy：按固定顺序执行，无论 LLM 返回什么内容
"""

import asyncio
import uuid
from typing import Any, AsyncIterator

from nexus.core.agent.agent import Agent
from nexus.core.context.context import ExecutionContext
from nexus.core.executor.actions import (
    Action,
    LLMCallAction,
    ToolCallAction,
    FinishAction,
    ErrorAction,
)
from nexus.core.executor.policy import ExecutionPolicy
from nexus.llm.base import (
    BaseLLM,
    LLMResponse,
    LLMChunk,
    UsageStats,
)
from nexus.tools.builtins import EchoTool


# ===========================================================================
# RolodexPolicy —— 自定义执行策略
# ===========================================================================
# 命名由来："Rolodex" 是旧式旋转名片盒，按固定顺序翻动名片。
# 这个策略也"按固定顺序翻页"——不根据 LLM 的 tool_calls 动态调整，
# 而是按预设的步骤序列执行。
#
# 设计要点：
# 1. next_action() 是唯一必须实现的方法，它是"决策函数"
# 2. 通过 context.state 读取当前执行状态（已完成几步、对话历史等）
# 3. 返回对应的 Action 子类实例，指示 Runtime 下一步做什么
# 4. 不应在 next_action 中修改 context.state（只读使用）
# 5. Policy 实例可以在多次 run 间复用（若内部状态允许）
#
# 适用场景：当你需要完全自定义 Agent 的决策流程时，例如：
# - 固定的审批流程（申请 → 审查 → 批准/拒绝）
# - 多阶段数据处理（提取 → 清洗 → 汇总 → 报告）
# - A/B 测试不同的 Agent 行为模式
# ===========================================================================

class RolodexPolicy(ExecutionPolicy):
    """顺序执行固定步骤的策略 —— 策略模式中的 ConcreteStrategy。

    执行序列（总共 3 步）：
    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
    │  LLM 初步推理  │ →  │  Echo 工具调用  │ →  │  LLM 最终回答  │ →  │   结束执行    │
    │  (step 0)     │    │  (step 1)     │    │  (step 2)     │    │  (step 3+)   │
    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘

    关键：无论 LLM 的响应中是否包含 tool_calls，我们都在步骤 1 强制调用 EchoTool。
    这与 ReActPolicy 完全不同——后者需要 LLM 主动请求 tool_calls 才会调用工具。

    内部状态说明：
    - _phase：跟踪当前处于哪个阶段（"initial_llm" / "echo_tool" / "final_llm" / "done"）
    - _echo_call_id：为 Echo 工具调用生成的唯一 ID
    - 这些状态在每次 run() 之间需要重置或由调用方创建新实例
    """

    def __init__(self) -> None:
        # 这 3 个是初始值，真正的重置在 next_action 首次调用时进行
        self._phase: str = "initial_llm"  # 当前执行阶段
        self._echo_call_id: str = ""        # Echo 工具调用的唯一标识

    # ------------------------------------------------------------------
    # next_action —— 核心决策方法
    # ------------------------------------------------------------------
    async def next_action(self, context: ExecutionContext) -> Action:
        """根据当前阶段返回下一步 Action。

        通过 self._phase 跟踪执行进度，每个阶段返回对应的 Action：
        - "initial_llm" → LLMCallAction（让 LLM 做初步推理）
        - "echo_tool"  → ToolCallAction（强制调用 echo 工具）
        - "final_llm"  → LLMCallAction（让 LLM 给出最终答案）
        - "done"       → FinishAction（任务完成）
        """
        state = context.state

        # ---- 阶段 0：首次调用 LLM ----
        # 发送包含 system prompt + user task 的消息列表，
        # 让 LLM 理解任务并做初步推理。
        if self._phase == "initial_llm":
            self._phase = "echo_tool"  # 推进到下一阶段（先推进再返回）
            print(f"      [RolodexPolicy] 阶段 0 → 发起首次 LLM 调用")

            # 获取已注册的工具 schema 列表，传给 LLM
            # 注意：虽然我们暂时期望 LLM 不调用工具，但传入 schema 是推荐做法
            tool_schemas = context.tool_executor.registry.to_openai_schemas()

            return LLMCallAction(
                messages=list(state.messages),  # 快照当前消息历史
                tools=tool_schemas,
            )

        # ---- 阶段 1：强制调用 Echo 工具 ----
        # 无论 LLM 第一步说了什么，我们都固定调用一次 echo 工具。
        # 这正是 RolodexPolicy 与 ReActPolicy 的核心区别：
        # ReActPolicy 会检查 LLM 响应中是否有 tool_calls，
        # 而 RolodexPolicy 按预设计划执行。
        elif self._phase == "echo_tool":
            self._phase = "final_llm"  # 推进到下一阶段
            self._echo_call_id = f"call_rolodex_echo_{uuid.uuid4().hex[:8]}"
            print(f"      [RolodexPolicy] 阶段 1 → 强制调用 Echo 工具")

            # 构造 ToolCallAction：
            # - tool_name: 必须是已注册工具的名称，否则 Runtime 会报错
            # - tool_call_id: 唯一标识，用于将 tool result 回写到消息历史
            # - arguments: 传递给工具 execute() 方法的参数字典
            return ToolCallAction(
                tool_name="echo",
                tool_call_id=self._echo_call_id,
                arguments={"message": "RolodexPolicy 说：Hello from the fixed step!"},
            )

        # ---- 阶段 2：再次调用 LLM，给出最终答案 ----
        # 此时 state.messages 中已经包含了：
        #   [system, user, assistant(第1次LLM回复), tool(echo结果)]
        # LLM 可以读取完整的上下文（包括 echo 工具的输出），给出最终回答。
        elif self._phase == "final_llm":
            self._phase = "done"
            print(f"      [RolodexPolicy] 阶段 2 → 发起最终 LLM 调用")

            tool_schemas = context.tool_executor.registry.to_openai_schemas()

            return LLMCallAction(
                messages=list(state.messages),
                tools=tool_schemas,
            )

        # ---- 阶段 3+：执行完成 ----
        # 所有固定步骤已完成，返回 FinishAction 让 Runtime 退出主循环。
        else:
            print(f"      [RolodexPolicy] 阶段 3 → 所有步骤完成，结束执行")
            return FinishAction(
                message="RolodexPolicy 固定步骤执行完毕",
                result=state.messages[-1].get("content", "") if state.messages else "",
            )


# ===========================================================================
# StepAwareMockLLM —— 能根据上下文返回不同内容的 Mock LLM
# ===========================================================================
# 真实使用时替换为：
#   from nexus.llm.providers.openai import OpenAIBackend
#   llm = OpenAIBackend(model="gpt-4o-mini", api_key="sk-xxx")
#
# 此 MockLLM 能根据收到的 messages 内容返回不同的回复：
# - 第 1 次 LLM 调用：做初步推理
# - 第 2 次 LLM 调用：看到 echo 工具结果后给出最终答案
# ===========================================================================

class StepAwareMockLLM(BaseLLM):
    """能感知上下文的 Mock LLM —— 根据对话历史返回不同内容。

    与 basic_agent.py 中简单的 MockLLM 不同，这个 MockLLM 检查 messages
    中是否包含 tool role（表示工具已执行），从而返回不同的回复。
    这模拟了真实 LLM 的行为：看到工具结果后给出更具体的答案。
    """

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._call_count += 1

        # 检查 messages 中是否已包含 tool role（工具执行结果）
        has_tool_result = any(
            msg.get("role") == "tool" for msg in messages
        )

        if not has_tool_result:
            # 第一次 LLM 调用：还没有工具结果，进行初步推理
            return LLMResponse(
                content="我理解你的任务。让我先用 Echo 工具确认一下系统状态。",
                usage=UsageStats(prompt_tokens=30, completion_tokens=15, total_tokens=45),
                model="mock-step-aware",
                finish_reason="stop",
            )
        else:
            # 第二次 LLM 调用：已收到工具结果，给出最终答案
            # 真实 LLM 会读取 tool role 消息中的内容，结合上下文生成答案
            return LLMResponse(
                content=(
                    "已完成 RolodexPolicy 演示流程！"
                    "Echo 工具返回了消息，确认系统运行正常。"
                    "整个过程按固定顺序执行：LLM推理 → 工具调用 → LLM最终回复。"
                    "这就是自定义 ExecutionPolicy 的核心价值——你可以完全控制 Agent 的决策流程。"
                ),
                usage=UsageStats(prompt_tokens=60, completion_tokens=40, total_tokens=100),
                model="mock-step-aware",
                finish_reason="stop",
            )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        full_response = await self.chat(messages, tools, **kwargs)
        for word in full_response.content.split():
            yield LLMChunk(delta_content=word + " ")


# ===========================================================================
# 主函数
# ===========================================================================

async def main() -> None:
    """演示如何使用 RolodexPolicy 替代默认的 ReActPolicy。"""

    # 1. 创建 LLM
    #    真实用法：llm = OpenAILLM(model="gpt-4o-mini")
    print("[1/5] 创建 StepAwareMockLLM（能感知上下文的 Mock LLM）...")
    llm = StepAwareMockLLM()

    # 2. 创建 RolodexPolicy 实例
    #    策略可以独立于 Agent 创建，方便单元测试和复用
    print("[2/5] 创建 RolodexPolicy...")
    policy = RolodexPolicy()

    # 3. 创建 Agent，并指定自定义 Policy
    #    若不指定 policy 参数，Agent 默认使用 ReActPolicy
    #    指定 policy 后，Agent 的调度循环将由自定义策略驱动
    print("[3/5] 创建 Agent（使用 RolodexPolicy）...")
    agent = Agent(
        llm=llm,
        policy=policy,  # ← 关键：传入自定义策略
        system_prompt="你是一个演示助手，正在展示 Nexus 框架的自定义策略功能。",
        max_steps=10,
        name="rolodex-demo-agent",
    )

    # 4. 注册 Echo 工具
    #    RolodexPolicy 的步骤 1 会强制调用 echo 工具
    print("[4/5] 注册 EchoTool...")
    agent.register_tool(EchoTool())

    # 5. 执行任务
    #    RolodexPolicy 按固定序列驱动执行：
    #    首次LLM → Echo工具 → 再次LLM → 结束
    print("[5/5] 执行任务：'请展示自定义策略的执行流程'...\n")
    state = await agent.run(task="请展示自定义策略的执行流程")

    # ---- 输出结果 ----
    print("\n" + "=" * 60)
    print("执行结果总览")
    print("=" * 60)
    print(f"  策略类型：RolodexPolicy（固定顺序策略）")
    print(f"  总步数：{state.current_step}")
    print(f"  消息数：{len(state.messages)}")
    print(f"  工具调用次数：{len(state.tool_calls)}")

    # 详细展示每一步
    print(f"\n执行步骤详情：")
    print("-" * 60)
    for i, step in enumerate(state.steps):
        print(f"  步骤 [{i}] 类型={step.step_type} (耗时约{step.duration_ms:.0f}ms)")
        if step.step_type == "llm_call":
            # 展示发送给 LLM 的消息数量，以及 LLM 的回复
            input_count = len(step.input_messages)
            output_preview = (
                step.output_content[:80] + "..."
                if len(step.output_content) > 80
                else step.output_content
            )
            print(f"        输入消息数：{input_count}")
            print(f"        LLM 回复：{output_preview}")
        elif step.step_type == "tool_call":
            for tc in step.tool_calls:
                print(f"        工具名：{tc.tool_name}")
                print(f"        参数：{tc.arguments}")
                print(f"        结果：{tc.result}")
                if tc.error:
                    print(f"        错误：{tc.error}")

    # 展示完整消息流转
    print(f"\n对话消息角色流转：")
    print("-" * 60)
    role_icons = {
        "system": "⚙️",
        "user": "👤",
        "assistant": "🤖",
        "tool": "🔧",
    }
    for i, msg in enumerate(state.messages):
        role = msg.get("role", "?")
        icon = role_icons.get(role, "❓")
        if role == "system":
            print(f"  [{i}] {icon} system → (系统提示词，省略)")
        elif role == "tool":
            content = str(msg.get("content", ""))
            print(f"  [{i}] {icon} tool → {content[:80]}")
        else:
            content = str(msg.get("content", ""))
            content_preview = content[:80] + "..." if len(content) > 80 else content
            print(f"  [{i}] {icon} {role} → {content_preview}")

    print(f"\n  ✓ RolodexPolicy 演示完成！")
    print(f"  这就是自定义 ExecutionPolicy 的完整流程。")


if __name__ == "__main__":
    # asyncio.run() 创建事件循环并运行 main() 协程
    asyncio.run(main())
