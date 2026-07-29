"""Agent State 数据结构定义。

定义 Agent 运行时状态的核心数据结构，包括工具调用记录、执行步骤和
完整的可序列化状态快照。State 作为 Agent 执行的唯一真实来源
（Single Source of Truth），Runtime 只修改 State，Policy 只读取 State
做决策，保证数据流单向。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nexus.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolCallRecord:
    """工具调用记录 —— 记录单次工具调用的完整信息。

    用于审计和调试，记录每次工具调用的名称、参数、返回值、错误信息和耗时。
    timestamp 和 duration_ms 分别记录调用发生的时刻和消耗的毫秒数。
    """

    tool_name: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0

    def serialize(self) -> dict[str, Any]:
        """序列化为 JSON-able dict。

        timestamp 转换为 ISO 8601 格式字符串，其余字段保持原样。
        """
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> "ToolCallRecord":
        """从 JSON-able dict 反序列化恢复 ToolCallRecord。

        timestamp 从 ISO 8601 字符串恢复为 datetime 对象。
        缺失字段回退到 dataclass 默认值。
        """
        return cls(
            tool_name=data["tool_name"],
            arguments=data["arguments"],
            result=data.get("result"),
            error=data.get("error"),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.now(timezone.utc),
            duration_ms=data.get("duration_ms", 0.0),
        )


@dataclass
class Step:
    """执行步骤 —— 记录单个执行步骤（LLM 调用或 Tool 调用）。

    每个 Step 对应 Agent 执行流程中的一个离散操作：LLM 推理调用、
    工具调用、计划生成或反思阶段。step_id 唯一标识每一步，
    tool_calls 记录该步内发生的所有工具调用。
    """

    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    step_type: str = ""  # "llm_call" | "tool_call" | "plan" | "reflect"
    input_messages: list[dict[str, Any]] = field(default_factory=list)
    output_content: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0

    def serialize(self) -> dict[str, Any]:
        """序列化为 JSON-able dict。

        嵌套的 ToolCallRecord 列表递归序列化，timestamp 转换为 ISO 8601 字符串。
        """
        return {
            "step_id": self.step_id,
            "step_type": self.step_type,
            "input_messages": self.input_messages,
            "output_content": self.output_content,
            "tool_calls": [tc.serialize() for tc in self.tool_calls],
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> "Step":
        """从 JSON-able dict 反序列化恢复 Step。

        嵌套的 ToolCallRecord 列表递归反序列化，timestamp 从 ISO 8601 字符串恢复。
        缺失字段回退到 dataclass 默认值。
        """
        return cls(
            step_id=data.get("step_id", str(uuid.uuid4())),
            step_type=data.get("step_type", ""),
            input_messages=data.get("input_messages", []),
            output_content=data.get("output_content", ""),
            tool_calls=[
                ToolCallRecord.deserialize(tc) for tc in data.get("tool_calls", [])
            ],
            timestamp=datetime.fromisoformat(data["timestamp"])
            if "timestamp" in data
            else datetime.now(timezone.utc),
            duration_ms=data.get("duration_ms", 0.0),
        )


@dataclass
class AgentState:
    """Agent 运行时状态 —— 完整的可序列化状态快照。

    设计思路
    --------
    State 作为 Agent 执行的唯一真实来源（Single Source of Truth）：
    - Runtime 只修改 State（写入执行进度、工具调用结果等）
    - Policy 只读取 State（基于当前状态做决策）
    - 保证数据流单向：Runtime → State → Policy

    字段说明
    --------
    task : str
        当前任务描述，驱动 Agent 行为的顶层目标。
    steps : list[Step]
        历史执行步骤列表，记录完整的执行轨迹，用于审计和回溯。
    tool_calls : list[ToolCallRecord]
        全局工具调用记录（展平视图），方便快速查询所有工具调用，
        与各 Step 内的 tool_calls 是不同视角的同一数据。
    memory_refs : list[str]
        Memory 引用 ID 列表，指向外部 Memory 系统中存储的上下文数据。
    intermediate_results : dict[str, Any]
        中间结果字典，存储步骤间的临时计算结果，供后续步骤引用。
    variables : dict[str, Any]
        运行时变量，Policy 可在执行过程中动态设置和读取的键值对。
    messages : list[dict[str, Any]]
        对话消息历史，按 LLM API 格式存储（role + content），
        每次 LLM 调用时作为上下文传入。
    run_id : str
        本次运行的唯一标识符。
    created_at : datetime
        State 创建时间。
    current_step : int
        当前执行步骤序号，用于跟踪执行进度和断点恢复。

    serialize / deserialize 使用场景
    --------------------------------
    - 暂停恢复：Agent 执行中断后，将 State 序列化持久化，恢复时反序列化继续执行。
    - 跨进程传递：在多进程 / 分布式架构中，将 State 序列化后通过消息队列传递。
    - 持久化：将执行历史写入数据库或文件，支持事后审计和回放。
    - 调试与可观测性：导出 State 快照用于调试和分析 Agent 行为。
    """

    task: str = ""
    steps: list[Step] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    intermediate_results: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_step: int = 0

    # ------------------------------------------------------------------
    # 状态修改方法
    # ------------------------------------------------------------------

    def add_step(self, step: Step) -> None:
        """添加执行步骤到历史。

        将 step 追加到 steps 列表末尾，保留完整执行轨迹。
        """
        self.steps.append(step)

    def add_tool_call(self, record: ToolCallRecord) -> None:
        """添加工具调用记录。

        将 ToolCallRecord 追加到全局 tool_calls 列表，
        与各 Step 内的 tool_calls 保持数据同步。
        """
        self.tool_calls.append(record)

    def add_message(self, role: str, content: str) -> None:
        """向消息历史追加一条消息。

        按 LLM API 标准格式存储（role + content）。

        Parameters
        ----------
        role : str
            消息角色，如 "system"、"user"、"assistant"、"tool"。
        content : str
            消息正文。
        """
        self.messages.append({"role": role, "content": content})

    # ------------------------------------------------------------------
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """序列化 State 为 JSON-able dict。

        序列化格式与约定
        ----------------
        - 所有 dataclass 字段递归转为纯 dict/list/str/int/float 组合。
        - datetime 字段统一转为 ISO 8601 格式字符串（含时区信息）。
        - 嵌套的 Step / ToolCallRecord 递归调用各自的 serialize() 方法。
        - 输出可直接传入 json.dumps() 或 json.dump()。
        """
        return {
            "task": self.task,
            "steps": [s.serialize() for s in self.steps],
            "tool_calls": [tc.serialize() for tc in self.tool_calls],
            "memory_refs": self.memory_refs,
            "intermediate_results": self.intermediate_results,
            "variables": self.variables,
            "messages": self.messages,
            "run_id": self.run_id,
            "created_at": self.created_at.isoformat(),
            "current_step": self.current_step,
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> "AgentState":
        """从 JSON-able dict 反序列化恢复 State。

        反序列化格式与约定
        ------------------
        - ISO 8601 字符串恢复为 aware datetime 对象。
        - 嵌套的 Step / ToolCallRecord 递归调用各自的 deserialize() 类方法。
        - 输入可来自 json.loads() 或 json.load() 的输出。
        - 缺失字段回退到 dataclass 声明的默认值，保证向后兼容。

        Parameters
        ----------
        data : dict[str, Any]
            待反序列化的字典数据。

        Returns
        -------
        AgentState
            恢复后的 AgentState 实例。
        """
        return cls(
            task=data.get("task", ""),
            steps=[Step.deserialize(s) for s in data.get("steps", [])],
            tool_calls=[
                ToolCallRecord.deserialize(tc) for tc in data.get("tool_calls", [])
            ],
            memory_refs=data.get("memory_refs", []),
            intermediate_results=data.get("intermediate_results", {}),
            variables=data.get("variables", {}),
            messages=data.get("messages", []),
            run_id=data.get("run_id", str(uuid.uuid4())),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(timezone.utc),
            current_step=data.get("current_step", 0),
        )
