"""ReAct (Reasoning + Acting) 执行策略 —— 默认的思考-行动交替模式。

设计思路
--------
ReAct 是最经典的 Agent 模式：LLM 生成推理文本，需要时调用工具获取信息，
工具结果回馈给 LLM 继续推理，循环直到 LLM 认为任务完成。

状态转移图
----------

    START
      │
      ▼
  ┌─────────────┐    无 tool_calls    ┌──────────────┐
  │ LLMCallAction│ ──────────────────→ │ FinishAction  │
  └──────┬──────┘                     └──────────────┘
         │ 有 tool_calls
         ▼
  ┌──────────────┐                     ┌─────────────┐
  │ToolCallAction │ ─── 结果回写后 ──→ │ LLMCallAction│（循环）
  └──────────────┘                     └─────────────┘

  max_steps 超限或异常 → ErrorAction

关键决策：
- 为什么首次要返回 LLMCallAction 而非直接发最终结果？
  因为用户输入需要经过 LLM 理解、可能需要工具辅助，不能预设 LLM 不需要工具。

- 为什么工具调用后要再返回 LLMCallAction？
  工具结果需要 LLM 阅读理解后才能决定下一步（继续调工具 or 输出最终答案）。

- max_steps 兜底：
  防止 LLM 无限循环调工具。若达到 max_steps 且 LLM 仍返回 tool_calls，
  返回 ErrorAction 终止执行。
"""

from __future__ import annotations

import json
from typing import Any

from nexus.core.executor.policy import ExecutionPolicy
from nexus.core.executor.actions import (
    Action,
    LLMCallAction,
    ToolCallAction,
    FinishAction,
    ErrorAction,
)
from nexus.core.context.context import ExecutionContext
from nexus.logging import get_logger

logger = get_logger(__name__)


class ReActPolicy(ExecutionPolicy):
    """ReAct 执行策略 —— 默认的思考-行动交替模式。

    内部状态：_step_count 跟踪已执行的步骤数（用于 max_steps 控制）。

    注意：_step_count 是 Policy 的内部状态（非 AgentState 的 current_step），
    两者独立 —— State 的 current_step 由 Runtime 在每次 Action 执行前递增并保存到 State，
    Policy 的 _step_count 仅用于决策逻辑（max_steps 判断）。
    """

    def __init__(self, max_steps: int = 30) -> None:
        self.max_steps = max_steps
        self._step_count: int = 0
        # 缓存 LLM 本轮返回的待执行 tool_calls 队列。
        # 当 LLM 一次返回多个 tool_calls 时，逐一弹出执行；
        # 全部执行完毕后回到 LLM 继续推理。
        # 每个元素为 (tool_name, tool_call_id, arguments_dict)。
        self._pending_tool_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def next_action(self, context: ExecutionContext) -> Action:
        """根据当前执行上下文决定下一步 Action。

        决策逻辑通过 ``context.state.steps`` 中最后一步的 ``step_type``
        推断"上一个 Action 是什么"，分情况处理。这是 Policy 核心的状态机实现。

        Parameters
        ----------
        context : ExecutionContext
            运行时上下文，包含 state、llm、tool_executor 等组件。

        Returns
        -------
        Action
            下一步要执行的操作指令。
        """
        state = context.state
        steps = state.steps

        # ---- 首次调用：无历史步骤，直接发起 LLM 调用 ----
        if not steps:
            self._step_count += 1
            logger.info(
                "ReAct loop start (step %d/%d)",
                self._step_count,
                self.max_steps,
            )
            return LLMCallAction(
                messages=list(state.messages),
                tools=self._get_tool_schemas(context),
            )

        last_step_type = steps[-1].step_type

        # ---- LLM 刚返回响应：检查是否包含 tool_calls ----
        if last_step_type == "llm_call":
            return self._handle_after_llm(context)

        # ---- 工具刚执行完：检查是否还有待执行工具，或回到 LLM ----
        if last_step_type == "tool_call":
            return self._handle_after_tool(context)

        # 安全兜底：未知 step_type（Runtime 错误或未来扩展类型）
        logger.warning(
            "Unknown step_type '%s' in last step, falling back to FinishAction",
            last_step_type,
        )
        return FinishAction(message="Execution completed")

    # ------------------------------------------------------------------
    # 内部决策方法
    # ------------------------------------------------------------------

    def _handle_after_llm(self, context: ExecutionContext) -> Action:
        """LLM 响应回来后的决策。

        检查最后一条 assistant 消息是否包含 tool_calls：
        - 有 tool_calls → 解析并缓存到 _pending_tool_calls，逐一返回 ToolCallAction
        - 无 tool_calls → LLM 已完成推理，返回 FinishAction
        """
        assistant_msg = context.state.messages[-1]
        tool_calls = assistant_msg.get("tool_calls")

        # 无工具调用：LLM 直接完成推理，输出最终答案
        if not tool_calls:
            logger.info("LLM finished without tool_calls, returning FinishAction")
            return FinishAction(result=assistant_msg.get("content", ""))

        # 解析 tool_calls 为内部格式。
        # LLM 返回的 arguments 可能是 JSON 字符串（OpenAI 格式）或已解析的 dict
        # （取决于 Runtime 的 LLM 适配层实现），这里兼容两种格式。
        try:
            parsed: list[tuple[str, str, dict[str, Any]]] = []
            for tc in tool_calls:
                fn = tc["function"]
                raw_args = fn["arguments"]
                args: dict[str, Any] = (
                    json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                )
                parsed.append((fn["name"], tc["id"], args))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Failed to parse tool_calls from LLM response: %s", e)
            return ErrorAction(error=f"Failed to parse tool_calls: {e}")

        self._pending_tool_calls = parsed

        # 弹出第一个 tool_call 并返回
        name, call_id, args = self._pending_tool_calls.pop(0)
        logger.info(
            "Dispatching tool call #%d/%d: '%s' (id=%s)",
            len(steps_counted := context.state.steps) + 1,  # 仅用于日志可读性
            len(steps_counted) + len(self._pending_tool_calls) + 1,
            name,
            call_id,
        )
        return ToolCallAction(tool_name=name, tool_call_id=call_id, arguments=args)

    def _handle_after_tool(self, context: ExecutionContext) -> Action:
        """工具执行完毕后的决策。

        检查 _pending_tool_calls 队列：
        - 队列非空 → 同一批次还有工具未执行，返回下一个 ToolCallAction
        - 队列为空 → 所有工具执行完毕
          - _step_count >= max_steps → 返回 ErrorAction 终止
          - 否则 → 返回 LLMCallAction，将工具结果回馈给 LLM 继续推理
        """
        # 同批次还有未执行工具，继续逐一派发
        if self._pending_tool_calls:
            name, call_id, args = self._pending_tool_calls.pop(0)
            logger.info(
                "Dispatching next tool call '%s' (id=%s), %d remaining in batch",
                name,
                call_id,
                len(self._pending_tool_calls),
            )
            return ToolCallAction(tool_name=name, tool_call_id=call_id, arguments=args)

        # 所有工具执行完毕，检查 max_steps 是否超限
        if self._step_count >= self.max_steps:
            logger.warning(
                "ReAct max_steps (%d) exceeded at step %d, returning ErrorAction",
                self.max_steps,
                self._step_count,
            )
            return ErrorAction(error="Max steps exceeded")

        # 未超限：回到 LLM 继续推理。
        # _step_count 仅在进入 LLM 调用阶段时递增，工具调用不计入。
        self._step_count += 1
        logger.info(
            "All tool calls done, returning to LLM (step %d/%d)",
            self._step_count,
            self.max_steps,
        )
        return LLMCallAction(
            messages=list(context.state.messages),
            tools=self._get_tool_schemas(context),
        )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _get_tool_schemas(self, context: ExecutionContext) -> list[dict[str, Any]]:
        """从 ToolExecutor 的 registry 获取工具 schema 列表（OpenAI 格式）。

        遍历 ToolRegistry 中所有已注册工具，调用各工具的
        ``to_openai_schema()`` 方法生成 function-calling 兼容的 schema 列表，
        可直接嵌入 LLM API 请求的 ``tools`` 参数。
        """
        return context.tool_executor.registry.to_openai_schemas()
