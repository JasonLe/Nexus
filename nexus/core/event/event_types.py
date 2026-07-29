"""Agent 运行时事件类型枚举。

设计思路
--------
- 使用枚举而非字符串常量，防止拼写错误并提供 IDE 自动补全。
- 覆盖 Agent 执行全生命周期：运行、LLM 调用、工具调用、错误、结束。
- 新增事件类型只需在此枚举中添加成员，EventBus 和下游 handler 即可感知。
- EventType 作为事件路由的 key，EventBus 内部使用它建立 handler 索引。

扩展点
------
- 可在各 BEFORE/AFTER 对之间插入更细粒度的事件（如 BEFORE_RETRY、ON_STREAMING）。
- 可按需添加模块专属事件（如 PLUGIN_LOADED、SESSION_CREATED）。
"""

from enum import Enum, auto


class EventType(Enum):
    """Agent 运行时事件类型枚举 - 覆盖 Agent 执行全生命周期。

    每个事件类型对应 Runtime 中的一个关键生命周期节点，
    EventBus 以此枚举值作为事件路由的索引 key。

    事件触发顺序（典型一次 Agent 运行）：
    BEFORE_AGENT_RUN -> [BEFORE_LLM_CALL -> AFTER_LLM_CALL -> BEFORE_TOOL_CALL -> AFTER_TOOL_CALL]* -> AFTER_AGENT_RUN -> ON_FINISH
    若中途发生异常，触发 ON_ERROR。
    """

    BEFORE_AGENT_RUN = auto()
    """Agent 执行开始前触发。payload 含 agent_name、session_id、run_id。"""

    AFTER_AGENT_RUN = auto()
    """Agent 执行完成后触发。payload 含 agent_name、result、run_id。"""

    BEFORE_LLM_CALL = auto()
    """LLM 调用前触发。payload 含 model、provider、messages、tools。"""

    AFTER_LLM_CALL = auto()
    """LLM 调用后触发。payload 含 model、provider、response、usage。"""

    BEFORE_TOOL_CALL = auto()
    """工具调用前触发。payload 含 tool_name、tool_call_id、args。"""

    AFTER_TOOL_CALL = auto()
    """工具调用后触发。payload 含 tool_name、tool_call_id、result、error。"""

    ON_ERROR = auto()
    """运行时发生异常时触发。payload 含 error、traceback、recoverable、run_id。"""

    ON_FINISH = auto()
    """运行时完全结束时触发（无论成功或失败）。payload 含 run_id、final_state。"""
