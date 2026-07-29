r"""Nexus 自定义工具示例 —— 展示如何定义和注册自定义工具

本示例展示两种创建工具的方式：
1. 使用 @tool 装饰器 —— 推荐方式，声明式定义元数据
2. 手动继承 BaseTool —— 灵活方式，适合需要动态元数据的场景

运行方式：
    cd d:\Nexus && python examples\custom_tool.py

核心要点：
- 每个工具都需要定义 name、description、schema 和 execute()
- execute() 应返回 ToolResult（成功或失败），不应抛出异常
- 工具注册到 Agent 后，LLM 通过 function calling 机制发现并调用
"""

import asyncio
from typing import Any, AsyncIterator

from nexus.core.agent.agent import Agent
from nexus.llm.base import (
    BaseLLM,
    LLMResponse,
    LLMChunk,
    UsageStats,
    ToolCall,
)
from nexus.tools.base import BaseTool, ToolResult
from nexus.tools.decorators import tool


# ===========================================================================
# 方式一：使用 @tool 装饰器 —— 推荐用于大多数场景
# ===========================================================================
# @tool 装饰器在类定义时自动注入 name/description/schema 三个属性，
# 免去手动编写 property getter 的样板代码。元数据集中在装饰器参数中，
# 视觉上更清晰，IDE 也能正确推断类型。
# ===========================================================================

@tool(
    name="weather",
    description="查询指定城市的天气信息。输入城市名称，返回温度、天气状况和湿度。",
    schema={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，如 '北京'、'上海'、'Tokyo'",
            },
        },
        "required": ["city"],
    },
)
class WeatherTool(BaseTool):
    """天气查询工具 —— 使用 @tool 装饰器定义。

    设计要点：
    - @tool 装饰器自动设置 name/description/schema，无需手动覆写 property
    - 只需实现 execute(args) 异步方法
    - execute 内部不应抛出异常，用 ToolResult.fail() 返回错误信息
    """

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """查询城市天气（模拟数据）。

        真实用法：调用 OpenWeatherMap、和风天气等 API 获取实时数据。
        示例中返回模拟数据，展示返回格式。

        Parameters
        ----------
        args : dict
            包含 "city" 键，值为城市名称字符串。

        Returns
        -------
        ToolResult
            成功时 data 包含温度、天气、湿度等信息；
            失败时 success=False 并包含 error 描述。
        """
        city = args.get("city", "未知城市")

        # 真实用法：await fetch_weather_api(city)
        # 这里用模拟数据代替
        weather_data = {
            "北京": {"temperature": 25, "condition": "晴", "humidity": "45%"},
            "上海": {"temperature": 28, "condition": "多云", "humidity": "60%"},
            "Tokyo": {"temperature": 22, "condition": "小雨", "humidity": "75%"},
        }

        if city in weather_data:
            return ToolResult.ok(
                data={
                    "city": city,
                    **weather_data[city],
                    "source": "模拟数据（需替换为真实 API）",
                },
                tool_name=self.name,
            )
        else:
            return ToolResult.fail(
                error=f"未找到城市 '{city}' 的天气数据（支持的城市：北京、上海、Tokyo）",
                tool_name=self.name,
            )


# ===========================================================================
# 方式二：手动继承 BaseTool —— 适合需要动态元数据的场景
# ===========================================================================
# 当工具的名称/描述/schema 需要在运行时动态确定时（如根据配置文件、
# 数据库查询结果生成），直接覆写 property 更灵活。
# ===========================================================================

class TimeTool(BaseTool):
    """时间查询工具 —— 手动继承 BaseTool 实现。

    适用场景：
    - 工具的某些元数据需要运行时动态计算
    - 需要额外的内部状态管理（如数据库连接、缓存）
    - 需要覆写 setup/teardown 管理资源生命周期

    与 @tool 装饰器的区别：
    - 需要手动覆写 name、description、schema 三个 property
    - 元数据分散在代码中，但可以包含动态逻辑
    """

    # ---- 元数据定义 ----
    @property
    def name(self) -> str:
        return "get_time"

    @property
    def description(self) -> str:
        return "获取当前时间信息，包括日期、星期、精确时间。不需要任何参数。"

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    # ---- 生命周期（可选） ----
    async def setup(self) -> None:
        """工具初始化 —— 在此建立连接、预热缓存等。

        ToolExecutor 在首次调用 execute 前会自动调用 setup()。
        若工具不需要初始化，可以不覆写。
        """
        # 真实用法：打开数据库连接池、初始化 HTTP 客户端等
        pass

    async def teardown(self) -> None:
        """工具清理 —— 在此关闭连接、释放资源等。

        ToolExecutor 在 shutdown 时自动调用 teardown()。
        若工具不持有外部资源，可以不覆写。
        """
        # 真实用法：关闭连接池、释放文件句柄等
        pass

    # ---- 核心逻辑 ----
    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """返回当前时间信息。"""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        return ToolResult.ok(
            data={
                "datetime": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": weekday_names[now.weekday()],
            },
            tool_name=self.name,
        )


# ===========================================================================
# SmartMockLLM —— 能模拟 tool_calls 的 LLM，用于演示工具调用流程
# ===========================================================================
# 真实使用 OpenAI 时：
#   from nexus.llm.providers.openai import OpenAIBackend
#   llm = OpenAIBackend(model="gpt-4o-mini", api_key="sk-xxx")
# OpenAI 会智能判断何时需要调用工具，并在 LLMResponse.tool_calls 中返回
# 工具调用请求。本例中我们用 SmartMockLLM 模拟这个行为。
# ===========================================================================

class SmartMockLLM(BaseLLM):
    """能模拟 tool_calls 的 Mock LLM。

    通过内部分步数计数器，模拟 LLM"先调用工具 → 再给出最终答案"的流程：
    - 第 1 次调用：返回 tool_calls，请求调用时间工具
    - 第 2 次调用：返回包含工具执行结果的最终回复
    - 后续调用：返回固定文本

    这样 ReActPolicy 会依次执行：LLM → Tool → LLM → Finish
    """

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0  # 记录被调用次数

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._call_count += 1
        call_id = self._call_count

        # 第 1 次调用：模拟 LLM 决定调用 get_time 工具
        # 真实场景中，OpenAI 会根据 system prompt 和用户任务自主决定
        # 是否以及何时调用工具。这里我们硬编码返回一个 ToolCall。
        if call_id == 1:
            tool_call = ToolCall(
                id="call_mock_time_001",
                name="get_time",
                arguments={},  # get_time 不需要参数
            )
            return LLMResponse(
                content="",  # 当 LLM 返回 tool_calls 时，content 通常为空
                tool_calls=[tool_call],
                usage=UsageStats(prompt_tokens=50, completion_tokens=20, total_tokens=70),
                model="mock-llm-v1",
                finish_reason="tool_calls",
            )

        # 第 2 次调用：收到了工具执行结果，给出最终回复
        # 此时 messages 中已包含 tool role 的消息（工具执行结果）
        # 真实 LLM 会根据上下文给出最终答案
        return LLMResponse(
            content="根据查询结果，现在是 2026-07-29，周二。以上是示例中时间工具返回的模拟数据。",
            usage=UsageStats(prompt_tokens=80, completion_tokens=30, total_tokens=110),
            model="mock-llm-v1",
            finish_reason="stop",
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """流式版本 —— 仅返回最终文本，不模拟 tool_calls chunk。"""
        full_response = await self.chat(messages, tools, **kwargs)
        # 简单拆分模拟打字效果
        for word in full_response.content.split():
            yield LLMChunk(delta_content=word + " ")


# ===========================================================================
# 主函数
# ===========================================================================

async def main() -> None:
    """演示两种工具定义方式，并运行一次带工具调用的 Agent 任务。"""

    # 1. 创建 LLM
    #    真实用法：llm = OpenAILLM(model="gpt-4o-mini")
    #    用 SmartMockLLM 模拟 LLM 返回 tool_calls 的行为
    print("[1/5] 创建 SmartMockLLM（能模拟 tool_calls 的 Mock LLM）...")
    llm = SmartMockLLM()

    # 2. 创建 Agent
    print("[2/5] 创建 Agent...")
    agent = Agent(
        llm=llm,
        system_prompt="你是一个实用的助手。当用户询问天气或时间时，请调用相应的工具获取信息。",
        max_steps=10,
        name="tool-demo-agent",
    )

    # 3. 注册自定义工具
    #    - WeatherTool：使用 @tool 装饰器定义
    #    - TimeTool：手动继承 BaseTool 定义
    #    两种方式注册方式完全一致：agent.register_tool(实例)
    print("[3/5] 注册自定义工具...")
    agent.register_tool(WeatherTool())
    agent.register_tool(TimeTool())
    print(f"      已注册：weather（@tool 装饰器方式）")
    print(f"      已注册：get_time（手动继承 BaseTool 方式）")

    # 4. 执行任务
    #    SmartMockLLM 会在第 1 次 chat 中返回 ToolCall(name="get_time")，
    #    ReActPolicy 解析后返回 ToolCallAction，Runtime 执行 TimeTool.execute()，
    #    然后 Policy 再次请求 LLM，第 2 次 chat 返回最终回复。
    print("[4/5] 执行任务：'现在几点了？'...")
    state = await agent.run(task="现在几点了？")

    # 5. 查看结果
    print("[5/5] 查看执行结果：")
    print(f"      总步数：{state.current_step}")
    print(f"      消息数：{len(state.messages)}")
    print(f"      工具调用次数：{len(state.tool_calls)}")

    # 展示每一步发生了什么
    for i, step in enumerate(state.steps):
        print(f"\n      步骤 [{i}]：类型={step.step_type}")
        if step.step_type == "llm_call":
            print(f"              LLM 输出：{step.output_content[:60]}...")
        elif step.step_type == "tool_call":
            for tc in step.tool_calls:
                status = "✓ 成功" if not tc.error else f"✗ 失败: {tc.error}"
                print(f"              工具：{tc.tool_name} → {status}")
                print(f"              参数：{tc.arguments}")
                print(f"              结果：{tc.result}")

    # 展示对话历史中关键消息的角色流转
    print(f"\n      对话消息角色流转：")
    for i, msg in enumerate(state.messages):
        role = msg.get("role", "?")
        # 简洁展示
        if role == "system":
            print(f"        [{i}] system → (系统提示词)")
        elif role == "user":
            print(f"        [{i}] user → ask: {msg.get('content', '')}")
        elif role == "assistant":
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                tc_names = [tc["function"]["name"] for tc in tool_calls]
                print(f"        [{i}] assistant → tool_calls: {tc_names}")
            else:
                print(f"        [{i}] assistant → reply: {str(content)[:60]}")
        elif role == "tool":
            print(f"        [{i}] tool → result: {str(msg.get('content', ''))[:60]}")


if __name__ == "__main__":
    # asyncio.run() 是 Python 3.7+ 推荐的异步程序入口
    # 自动创建事件循环、运行协程、清理资源
    asyncio.run(main())
