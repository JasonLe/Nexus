"""Agent Runtime —— Agent 运行的调度引擎。

设计思路
--------
Runtime 是 Agent 的执行中枢，负责：

1. **生命周期管理** —— 创建 ExecutionContext、初始化 AgentState、清理资源
2. **调度循环** —— 非固定循环，通过 Policy 驱动：Policy.next_action → Runtime.execute
3. **事件派发** —— 在关键生命周期节点派发 8 类 Events
4. **状态管理** —— 每次 Action 执行后更新 AgentState
5. **错误处理** —— 捕获异常、派发 OnError、决定是否继续

调度模型
--------

    ┌─────────────────────────────────────────────┐
    │                Runtime.run()                  │
    │                                               │
    │  1. 派发 BEFORE_AGENT_RUN                      │
    │  2. 进入调度循环:                              │
    │     while True:                               │
    │       a. Policy.next_action(context) -> Action │
    │       b. 派发 BEFORE_(LLM|TOOL)                │
    │       c. 执行 Action (LLM / Tool / ...)       │
    │       d. 更新 State (steps / messages / vars)  │
    │       e. 派发 AFTER_(LLM|TOOL)                 │
    │       f. 若 FinishAction → 跳出循环            │
    │       g. 若 ErrorAction → 跳出循环             │
    │  3. 派发 ON_FINISH / AFTER_AGENT_RUN           │
    │  4. 返回最终结果                                │
    └─────────────────────────────────────────────┘

设计决策：
- Runtime 不预设 while True 循环次数，循环次数由 Policy.next_action() 返回的 Action 类型控制
- Runtime 只负责"如何执行某个 Action"，不负责"决定下一步做什么"
- 每一步都更新 State，支持中途暂停/恢复
- 所有事件在 Runtime 层集中派发，Policy 层只做决策
"""

from __future__ import annotations

import json
import traceback
from typing import Any

from nexus.core.state.types import AgentState, Step, ToolCallRecord
from nexus.core.context.context import ExecutionContext
from nexus.core.event.event_bus import EventBus
from nexus.core.event.event_types import EventType
from nexus.core.event.types import Event
from nexus.core.executor.actions import (
    Action,
    LLMCallAction,
    ToolCallAction,
    FinishAction,
    ErrorAction,
)
from nexus.core.executor.policy import ExecutionPolicy
from nexus.llm.base import BaseLLM
from nexus.tools.registry import ToolRegistry
from nexus.tools.executor import ToolExecutor
from nexus.plugins.registry import PluginRegistry
from nexus.logging import get_logger

logger = get_logger(__name__)


class Runtime:
    """Agent Runtime —— Agent 运行的调度引擎。

    Runtime 是 Agent 的执行中枢，将"决策"（Policy）与"执行"（LLM / Tool）粘合在一起。
    它不决定下一步做什么（这是 Policy 的职责），只负责按照 Policy 的指令来执行具体的
    LLM 调用和工具调用，并在执行过程中维护 AgentState、派发生命周期事件、处理错误。

    核心设计特点
    ------------
    - **Policy 驱动**：所有执行步骤由 Policy.next_action() 返回的 Action 对象驱动，
      循环次数不受 Runtime 控制。
    - **集中事件派发**：所有 8 类事件（BEFORE_AGENT_RUN、AFTER_AGENT_RUN、BEFORE_LLM_CALL、
      AFTER_LLM_CALL、BEFORE_TOOL_CALL、AFTER_TOOL_CALL、ON_ERROR、ON_FINISH）
      在 Runtime 层统一派发，Policy 无需关心事件机制。
    - **状态单向流**：Runtime → State → Policy，State 的修改由 Runtime 完成，
      Policy 只读 State 做决策。
    - **每步可恢复**：每一步执行后立即更新 State，支持中途暂停/恢复、序列化/反序列化。

    Attributes
    ----------
    _event_bus : EventBus
        事件总线，负责事件发布与路由。
    _tool_registry : ToolRegistry
        工具注册中心，管理所有可用工具实例。
    _tool_executor : ToolExecutor
        工具执行器，封装校验 + 执行 + 记录流程。
    _plugin_registry : PluginRegistry
        插件注册中心，管理已安装插件的生命周期。
    """

    def __init__(self) -> None:
        self._event_bus = EventBus()
        self._tool_registry = ToolRegistry()
        self._tool_executor = ToolExecutor(self._tool_registry)
        self._plugin_registry = PluginRegistry()

    async def run(
        self,
        task: str,
        llm: BaseLLM,
        policy: ExecutionPolicy,
        initial_messages: list[dict[str, Any]] | None = None,
        variables: dict[str, Any] | None = None,
        max_steps: int = 20,
    ) -> AgentState:
        """启动 Agent 执行主循环。

        完整的执行流程：创建状态 → 派发启动事件 → 进入调度循环
        → 按 Action 类型分发执行 → 派发终止事件 → 返回最终状态。

        Parameters
        ----------
        task : str
            任务描述，驱动 Agent 行为的顶层目标。
        llm : BaseLLM
            LLM 实例，用于执行 LLM 推理调用。
        policy : ExecutionPolicy
            执行策略实例，负责决定下一步执行什么 Action。
        initial_messages : list[dict[str, Any]] | None
            初始消息列表（可选）。若提供，这些消息将在调度循环开始前追加到
            state.messages 中。常用于注入 system prompt 或历史上下文。
        variables : dict[str, Any] | None
            运行时变量（可选），存入 state.variables，供 Policy 和插件使用。
        max_steps : int
            最大执行步数上限，写入 ExecutionContext 供 Policy 读取。
            默认为 20 步，防止无限循环。

        Returns
        -------
        AgentState
            执行完成后的最终状态，包含完整的 messages、steps、tool_calls 等。
        """
        # ---- 1. 创建并初始化 AgentState ----
        state = AgentState(task=task)
        if variables:
            state.variables.update(variables)

        # ---- 2. 写入 initial_messages（如 system prompt）----
        if initial_messages:
            for msg in initial_messages:
                state.add_message(msg["role"], msg["content"])

        # ---- 3. 创建 ExecutionContext ----
        context = ExecutionContext(
            state=state,
            llm=llm,
            tool_executor=self._tool_executor,
            events=self._event_bus,
            max_steps=max_steps,
            variables=state.variables,
        )

        logger.info(
            "Runtime.run starting",
            extra={
                "run_id": state.run_id,
                "agent_name": "nexus",
            },
        )

        # ---- 4. 派发 BEFORE_AGENT_RUN 事件 ----
        await self._event_bus.publish(Event(
            type=EventType.BEFORE_AGENT_RUN,
            payload={
                "agent_name": "nexus",
                "session_id": state.run_id,
                "run_id": state.run_id,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        # ---- 5. 进入调度循环 ----
        try:
            while True:
                # 5a. Policy 决策：下一步做什么
                action = await policy.next_action(context)

                # 5b. 按 Action 类型分发执行
                if isinstance(action, LLMCallAction):
                    await self._execute_llm_call(context, action)
                elif isinstance(action, ToolCallAction):
                    await self._execute_tool_call(context, action)
                elif isinstance(action, FinishAction):
                    await self._handle_finish(context, action)
                    break  # 跳出循环
                elif isinstance(action, ErrorAction):
                    await self._handle_error(context, action)
                    break  # 跳出循环
                else:
                    # 未知 Action 类型：安全兜底，记录警告并以 ErrorAction 终止
                    logger.warning(
                        "Unknown action type '%s', terminating with ErrorAction",
                        type(action).__name__,
                        extra={"run_id": state.run_id, "step": state.current_step},
                    )
                    await self._handle_error(
                        context,
                        ErrorAction(error=f"Unknown action type: {type(action).__name__}"),
                    )
                    break

                # 5c. 步数递增
                state.current_step += 1

        except Exception as exc:
            # ---- 异常兜底 ----
            # 调度循环中任何未预期的异常都被捕获，转为 ErrorAction 处理
            logger.error(
                "Unhandled exception in Runtime.run loop",
                extra={"run_id": state.run_id, "step": state.current_step},
                exc_info=True,
            )
            err_action = ErrorAction(error=str(exc), exception=exc)
            await self._handle_error(context, err_action)

        # ---- 6. 派发 AFTER_AGENT_RUN 事件 ----
        await self._event_bus.publish(Event(
            type=EventType.AFTER_AGENT_RUN,
            payload={
                "agent_name": "nexus",
                "result": state,
                "run_id": state.run_id,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        logger.info(
            "Runtime.run finished",
            extra={
                "run_id": state.run_id,
                "total_steps": state.current_step,
                "total_messages": len(state.messages),
            },
        )

        return state

    # ------------------------------------------------------------------
    # Action 执行方法
    # ------------------------------------------------------------------

    async def _execute_llm_call(
        self, context: ExecutionContext, action: LLMCallAction
    ) -> None:
        """执行 LLM 调用 Action。

        流程：
        1. 派发 BEFORE_LLM_CALL 事件
        2. 调用 llm.chat()（非流式）获取 LLMResponse
        3. 将 assistant 消息添加到 state.messages（含 tool_calls 信息）
        4. 将 _last_llm_response 存入 state.variables 供 Policy 读取
        5. 创建 Step 记录
        6. 派发 AFTER_LLM_CALL 事件

        为什么要存储 _last_llm_response 到 variables？
        ReActPolicy 需要从 state.steps[-1] 读取 LLM 的 output 来解析 tool_calls。
        但 Step.output_content 只存储文本内容，不包含 tool_calls 结构化数据。
        因此将完整的 tool_calls 信息存入 state.variables["_last_llm_response"]，
        Policy 可从此处读取 LLM 返回的 tool_calls 列表。

        Parameters
        ----------
        context : ExecutionContext
            运行时上下文。
        action : LLMCallAction
            包含待发送的 messages 和 tools schema。
        """
        state = context.state

        # 1. 派发 BEFORE_LLM_CALL 事件
        await self._event_bus.publish(Event(
            type=EventType.BEFORE_LLM_CALL,
            payload={
                "model": getattr(context.llm, "model", "unknown"),
                "provider": type(context.llm).__name__,
                "messages": action.messages,
                "tools": action.tools,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        logger.info(
            "Calling LLM",
            extra={
                "run_id": state.run_id,
                "step": state.current_step,
                "model": getattr(context.llm, "model", "unknown"),
                "message_count": len(action.messages),
            },
        )

        # 2. 调用 LLM
        response = await context.llm.chat(
            messages=action.messages,
            tools=action.tools,
        )

        # 3. 将 assistant 消息写入 state.messages
        assistant_msg: dict[str, Any] = {"role": "assistant"}

        # 添加文本内容（可能为空，当 LLM 返回 tool_calls 时）
        if response.content:
            assistant_msg["content"] = response.content

        # 添加 tool_calls（若 LLM 请求调用工具）
        if response.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": (
                            tc.arguments
                            if isinstance(tc.arguments, str)
                            else json.dumps(tc.arguments, ensure_ascii=False)
                        ),
                    },
                }
                for tc in response.tool_calls
            ]

        state.messages.append(assistant_msg)

        # 4. 存储最后 LLM 响应到 variables，供 Policy（如 ReActPolicy）读取 tool_calls
        state.variables["_last_llm_response"] = {
            "content": response.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ],
        }

        # 5. 创建 Step 记录
        step = Step(
            step_type="llm_call",
            input_messages=list(action.messages),  # 快照：记录发送给 LLM 的消息
            output_content=response.content,
        )
        state.add_step(step)

        # 6. 派发 AFTER_LLM_CALL 事件
        await self._event_bus.publish(Event(
            type=EventType.AFTER_LLM_CALL,
            payload={
                "model": response.model or getattr(context.llm, "model", "unknown"),
                "provider": type(context.llm).__name__,
                "response": response,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                } if response.usage else None,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        logger.info(
            "LLM call completed",
            extra={
                "run_id": state.run_id,
                "step": state.current_step,
                "model": response.model,
                "finish_reason": response.finish_reason,
                "has_tool_calls": bool(response.tool_calls),
                "tool_call_count": len(response.tool_calls),
            },
        )

    async def _execute_tool_call(
        self, context: ExecutionContext, action: ToolCallAction
    ) -> None:
        """执行工具调用 Action。

        流程：
        1. 派发 BEFORE_TOOL_CALL 事件
        2. 调用 tool_executor.execute(tool_name, tool_call_id, arguments)
        3. 创建 ToolCallRecord 并添加到 state.tool_calls
        4. 将 tool result 消息追加到 state.messages（OpenAI tool role 格式）
        5. 更新 state.variables["_last_tool_result"]
        6. 创建 Step 记录
        7. 派发 AFTER_TOOL_CALL 事件

        Parameters
        ----------
        context : ExecutionContext
            运行时上下文。
        action : ToolCallAction
            包含 tool_name、tool_call_id、arguments。
        """
        state = context.state

        # 1. 派发 BEFORE_TOOL_CALL 事件
        await self._event_bus.publish(Event(
            type=EventType.BEFORE_TOOL_CALL,
            payload={
                "tool_name": action.tool_name,
                "tool_call_id": action.tool_call_id,
                "args": action.arguments,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        logger.info(
            "Executing tool",
            extra={
                "run_id": state.run_id,
                "step": state.current_step,
                "tool_name": action.tool_name,
                "tool_call_id": action.tool_call_id,
            },
        )

        # 2. 调用 ToolExecutor
        result = await self._tool_executor.execute(
            tool_name=action.tool_name,
            tool_call_id=action.tool_call_id,
            arguments=action.arguments,
        )

        # 3. 创建 ToolCallRecord
        record = ToolCallRecord(
            tool_name=action.tool_name,
            arguments=action.arguments,
            result=result.data,
            error=result.error,
            duration_ms=result.duration_ms,
        )
        state.add_tool_call(record)

        # 4. 将 tool 结果消息追加到 state.messages
        state.messages.append({
            "role": "tool",
            "tool_call_id": action.tool_call_id,
            "content": str(result.data) if result.data is not None else result.error or "",
        })

        # 5. 更新 _last_tool_result
        state.variables["_last_tool_result"] = result.data

        # 6. 创建 Step 记录
        step = Step(
            step_type="tool_call",
            tool_calls=[record],
        )
        state.add_step(step)

        # 7. 派发 AFTER_TOOL_CALL 事件
        await self._event_bus.publish(Event(
            type=EventType.AFTER_TOOL_CALL,
            payload={
                "tool_name": action.tool_name,
                "tool_call_id": action.tool_call_id,
                "result": result.data,
                "error": result.error,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        log_msg = (
            "Tool call succeeded"
            if result.success
            else "Tool call failed"
        )
        logger.info(
            log_msg,
            extra={
                "run_id": state.run_id,
                "step": state.current_step,
                "tool_name": action.tool_name,
                "tool_call_id": action.tool_call_id,
                "duration_ms": result.duration_ms,
            },
        )

    async def _handle_finish(
        self, context: ExecutionContext, action: FinishAction
    ) -> None:
        """处理 FinishAction：记录最终结果并派发 ON_FINISH 事件。

        这是正常终止路径。Policy 判断任务已完成（LLM 输出最终答案或达到自然终止条件），
        Runtime 收到 FinishAction 后派发 ON_FINISH 事件，然后退出主循环。

        Parameters
        ----------
        context : ExecutionContext
            运行时上下文。
        action : FinishAction
            包含最终结果和终止消息。
        """
        state = context.state

        # 将 finish 结果写入 state
        state.intermediate_results["final_result"] = action.result
        state.intermediate_results["finish_message"] = action.message

        await self._event_bus.publish(Event(
            type=EventType.ON_FINISH,
            payload={
                "run_id": state.run_id,
                "final_state": state.serialize(),
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        logger.info(
            "Agent finished",
            extra={
                "run_id": state.run_id,
                "step": state.current_step,
                "finish_message": action.message,
            },
        )

    async def _handle_error(
        self, context: ExecutionContext, action: ErrorAction
    ) -> None:
        """处理 ErrorAction：记录错误信息并派发 ON_ERROR 事件。

        这是异常终止路径。Policy 判断发生不可恢复的错误（如 max_steps 超限、
        tool_calls 解析失败等），或 Runtime 自身在调度循环中捕获异常，
        都会转为 ErrorAction 处理。Runtime 收到后派发 ON_ERROR 事件并退出主循环。

        Parameters
        ----------
        context : ExecutionContext
            运行时上下文。
        action : ErrorAction
            包含错误描述和可选的异常对象。
        """
        state = context.state

        # 记录错误信息到 state
        state.intermediate_results["error"] = action.error
        if action.exception:
            state.intermediate_results["exception"] = str(action.exception)
            state.intermediate_results["traceback"] = traceback.format_exc()

        await self._event_bus.publish(Event(
            type=EventType.ON_ERROR,
            payload={
                "error": action.exception if action.exception else action.error,
                "traceback": traceback.format_exc() if action.exception else action.error,
                "recoverable": False,
                "run_id": state.run_id,
            },
            run_id=state.run_id,
            step=state.current_step,
        ))

        logger.error(
            "Agent error",
            extra={
                "run_id": state.run_id,
                "step": state.current_step,
            },
            exc_info=bool(action.exception),
        )
