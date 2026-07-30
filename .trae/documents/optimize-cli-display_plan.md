# 优化 CLI 终端展示界面

## Summary

优化 Nexus CLI 的 REPL 终端展示，解决四个问题：1) 用户输入没有标签化展示；2) 每轮对话无明显分界；3) LLM 思考链过程没有展示；4) 思考链/工具调用/模型输出缺少颜色和样式区分。核心思路是为每种内容类型定义明确的视觉角色：用户消息（绿色标签）、思考链（灰色 dim italic 面板）、工具调用（青色面板）、AI 回复（亮色 Markdown），并用分隔线区分每轮对话。

## Current State Analysis

### 现有展示架构

- **display.py**：`DisplayManager` 封装 Rich 组件，已有 `render_response`（Markdown）、`render_tool_call`（Tree）、`render_thinking`（dim italic Text）、`render_error`（红色 Panel）、`show_spinner` 等方法
- **repl.py**：`Repl._execute_task()` 通过 EventBus 订阅事件驱动展示：
  - `BEFORE_LLM_CALL` → `show_info("Thinking...")` 只显示一行 dim 文字
  - `AFTER_LLM_CALL` → `render_response(response.content)` 直接渲染 Markdown，**不区分思考链和最终回复**
  - `BEFORE_TOOL_CALL` → `show_info(f"[{n}] 🔧 name(args)")` dim 文字
  - `AFTER_TOOL_CALL` → `show_info(f"  ✅ name: result")` dim 文字
  - 任务开始 → `show_info("")` 空行分隔
- **prompt_toolkit**：提示符只是 `[("class:prompt", "> ")]`，输入完成后内容直接推上去，没有在对话流中作为带标签的消息展示

### 已识别的问题

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| D1 | 用户输入没有标签化展示，输入完成后只是推到终端历史，和 prompt 混在一起 | repl.py:178-181 | 用户分不清哪些是自己的输入 |
| D2 | 每轮对话只有空行分隔，没有明显视觉边界 | repl.py:280 | 多轮对话时内容混在一起 |
| D3 | "Thinking..." 只是一行 dim 文字，LLM 在 tool_use 前的 reasoning text（思考链）被当作普通回复渲染 | repl.py:294,305-306; display.py:211-224 | 思考过程和最终回复没有视觉区分，render_thinking 方法定义了但 REPL 中从未调用 |
| D4 | 工具调用信息用 dim 灰色文字显示，和系统状态信息视觉权重相同 | repl.py:322,335 | 工具调用在对话流中不够醒目 |
| D5 | BEFORE_LLM_CALL 的 "Thinking..." 是 show_spinner 返回值但没被 `with` 包裹，spinner 实际未生效 | repl.py:294 vs main.py:163-164 (对比 _run_single 中 show_spinner 也是同样问题) | 用户看不到等待动画 |
| D6 | AI 回复没有角色标签（如 🤖 Nexus），和系统输出混在一起 | repl.py:305-306 | 难以区分 AI 输出和系统消息 |

## Proposed Changes

### 改动 1：增强 DisplayManager — 新增角色化渲染方法

**文件**：`nexus/cli/display.py`

新增/修改以下方法：

#### 1a. 新增 `render_user_message(text)` — 用户消息标签

```python
def render_user_message(self, text: str) -> None:
    """渲染用户消息，绿色标签 👤 You + 内容 Panel。"""
```
- 使用 Rich `Panel`，绿色边框（`border_style="green"`），title 为 `👤 You`
- 内容区域直接显示用户输入文本（自动换行，不做 Markdown 渲染以保留原始输入）
- Panel 宽度自适应终端宽度

#### 1b. 新增 `render_assistant_header()` — AI 回复标签

```python
def render_assistant_header(self) -> None:
    """渲染 AI 回复起始标签 🤖 Nexus。"""
```
- 不使用 Panel，直接打印一行 `Text("🤖 Nexus", style="bold cyan")`，作为 AI 回复区域的起始标记
- 紧跟在后面的 Markdown 渲染就是 AI 的内容

#### 1c. 改进 `render_thinking(text)` — 思考链用带竖线的 Panel

```python
def render_thinking(self, text: str) -> None:
    """渲染思考过程，灰色 dim italic + 左侧竖线 Panel。"""
```
- 改用 `Panel`，灰色边框（`border_style="dim"`），title 为 `🤔 Thinking`（dim 样式）
- Panel 内文本用 `dim italic` 样式
- 左侧视觉上形成"思考气泡"的感觉，与正式回复明确区分

#### 1d. 改进 `render_tool_call(...)` — 工具调用用青色 Panel

将现有 Tree 包裹在青色 Panel 中，Panel title 为 `🔧 Tool {n}: {tool_name}`。保持 Tree 的参数/结果展示，但外层增加 Panel 边框提升辨识度。

```python
def render_tool_call(self, tool_name, args, result, duration_ms=0, success=True, index=None) -> None:
```
- 新增 `index` 参数用于显示工具序号
- Panel 边框：成功用青色（`border_style="cyan"`），失败用红色（`border_style="red"`）
- Panel title：`🔧 Tool [{index}] {tool_name}`

#### 1e. 新增 `render_tool_call_start(tool_name, args, index)` — 工具调用中状态

```python
def render_tool_call_start(self, tool_name: str, args: dict, index: int) -> SpinnerContext:
    """工具调用开始时显示 spinner + 工具名，返回上下文管理器供 with 使用。"""
```
- 返回 `self.console.status(...)` 上下文管理器
- 显示 `🔧 Tool [{index}] {tool_name}({args_summary})...` 带 spinner 动画
- 调用方用 `with dm.render_tool_call_start(...)` 包裹工具执行

#### 1f. 新增 `render_divider()` — 轮次分隔线

```python
def render_divider(self) -> None:
    """渲染轮次分隔线（dim 灰色横线）。"""
```
- 打印一条 dim 灰色横线（用 `─` 字符填充终端宽度），作为两轮对话之间的视觉分隔

#### 1g. 修改 `show_spinner` 的使用方式

`show_spinner` 本身返回 `console.status()` 上下文管理器没问题，但在 repl.py 中需要正确用 `with` 语句包裹。

### 改动 2：改造 Repl._execute_task() 事件流展示

**文件**：`nexus/cli/repl.py`

#### 2a. 用户输入后立即渲染用户消息 Panel

在 `_execute_task(user_input)` 被调用时（即用户输入已提交），先调用 `self.display.render_user_message(user_input)` 把用户消息渲染到对话流中。这样用户输入不会消失在 prompt 行里，而是成为对话历史的一部分。

#### 2b. 每轮任务开始时渲染分隔线和 AI header

在执行 Agent.run() 之前：
1. 调用 `self.display.render_divider()` 渲染分隔线
2. 调用 `self.display.render_assistant_header()` 渲染 AI 标签

#### 2c. 区分思考链和最终回复

修改 `on_after_llm` 处理器逻辑：

```python
async def on_after_llm(event: Event) -> None:
    response = event.payload.get("response")
    usage = event.payload.get("usage")
    # ... 累计 token 用量 ...
    if response:
        has_tool_calls = bool(response.tool_calls)
        if has_tool_calls and response.content:
            # 有 tool_calls 时，content 是思考/推理过程
            self.display.render_thinking(response.content)
        elif not has_tool_calls and response.content:
            # 无 tool_calls 时，content 是最终回复
            self.display.render_response(response.content)
```

#### 2d. BEFORE_LLM_CALL 显示思考 spinner

```python
async def on_before_llm(event: Event) -> None:
    pass  # spinner 由调用方在 await agent.run() 外用 with 管理
```

由于 EventBus 是异步回调机制，无法直接在 BEFORE/AFTER 事件对之间维护一个 `with spinner:` 上下文。改为在 `_execute_task` 中使用 `self.display.show_spinner("🤔 Thinking...")` 包裹整个 `await self.agent.run(...)` 调用。但 spinner 会在工具调用期间持续显示，效果也不错（表示 Agent 在工作中）。

更精细的做法：在 AFTER_LLM 事件中如果发现有 tool_calls，停止前一个 spinner（但 Rich 的 status 上下文不支持外部停止）。因此折中方案：

- 使用 `console.status()` 包裹整个 `agent.run()`，显示 "🤔 Nexus is working..."
- 在各事件回调中直接打印内容，Rich 的 status 会自动在打印内容时暂停 spinner、打印后恢复
- 这样工具调用、思考链、最终回复都能正常渲染，spinner 在等待 LLM 响应时显示

#### 2e. BEFORE_TOOL_CALL / AFTER_TOOL_CALL 使用新的 Panel 样式

- `on_before_tool`：不再单独打印（spinner 已显示工作状态），只累加计数器
- `on_after_tool`：调用改进后的 `self.display.render_tool_call(...)`，传入 index、success 状态

#### 2f. 修改 prompt 提示符样式

将提示符从简单的 `> ` 改为更具辨识度的 `❯ `（仍然使用绿色加粗），保持简洁但有区分度。

### 改动 3：同步更新 _run_single 模式

**文件**：`nexus/cli/main.py`

`_run_single` 函数（非 REPL 的单次执行模式）也使用类似的事件展示逻辑，同步更新：
- 移除 on_before_llm 中未生效的 show_spinner（或正确用 with 包裹）
- 区分思考链和最终回复的渲染
- 工具调用使用新的 render_tool_call 样式

由于 _run_single 是一次性执行（不是多轮对话），不需要分隔线和用户消息 Panel（单次模式 prompt 就是命令行参数，已显示在命令行中）。

### 改动 4：样式设计总结

| 内容类型 | 视觉样式 | Rich 组件 |
|---------|---------|----------|
| 用户消息 | 绿色边框 Panel，title=👤 You | `Panel(border_style="green")` |
| 分隔线 | dim 灰色横线 | `Text("─" * width, style="dim")` |
| AI 标签 | 粗体青色行 | `Text("🤖 Nexus", style="bold cyan")` |
| 等待状态 | cyan spinner | `console.status(Spinner("dots"))` |
| 思考链 | dim 灰色边框 Panel，title=🤔 Thinking，内容 dim italic | `Panel(border_style="dim")` |
| 工具调用（成功） | 青色边框 Panel，title=🔧 Tool [n] name，Tree 展示 | `Panel(border_style="cyan")` + Tree |
| 工具调用（失败） | 红色边框 Panel，title=❌ Tool [n] name | `Panel(border_style="red")` + Tree |
| AI 最终回复 | 亮色 Markdown（无 Panel） | `Markdown(content)` |
| 错误 | 红色边框 Panel（已有） | `Panel(border_style="red", title="Error")` |

### 改动 5：更新测试

**文件**：`tests/test_cli_display.py`

现有测试检查基本功能。更新/新增测试：
- `test_render_user_message`：验证用户消息 Panel 渲染不报错
- `test_render_thinking_panel`：验证思考链 Panel 渲染（原来只是 Text，现在是 Panel）
- `test_render_tool_call_panel`：验证工具调用 Panel 渲染
- `test_render_divider`：验证分隔线不报错
- 其他现有测试确保不 regression

## Files Changed

| 文件 | 改动类型 |
|------|---------|
| `nexus/cli/display.py` | 新增 `render_user_message`、`render_assistant_header`、`render_tool_call_start`、`render_divider`；改进 `render_thinking`（改为 Panel）、`render_tool_call`（加 Panel 边框和 index）；保持其他方法不变 |
| `nexus/cli/repl.py` | 修改 `_execute_task()`：用户消息后渲染 Panel、加分隔线和 AI header、区分思考链/最终回复、改进工具调用展示、正确使用 spinner 包裹 agent.run()；微调 prompt 提示符 |
| `nexus/cli/main.py` | 同步更新 `_run_single()` 中的事件处理器，区分思考链和最终回复 |
| `tests/test_cli_display.py` | 更新/新增测试用例覆盖新的渲染方法 |

## Assumptions & Decisions

1. **不引入新依赖**：全部使用已有的 Rich 和 prompt_toolkit 组件
2. **思考链判定逻辑**：以 `response.tool_calls` 是否非空来区分——有 tool_calls 时的 content 是 reasoning/thinking 文本，无 tool_calls 时的 content 是最终回复。这符合 ReAct 模式的行为（LLM 在调用工具前输出推理文本，最后一轮输出最终答案）
3. **Spinner 策略**：用 `console.status()` 包裹整个 `agent.run()` 调用，事件回调中的 `console.print` 会自动暂停 spinner 后输出、输出后恢复。这是 Rich 的内置行为，无需手动管理
4. **不使用 Rich Live 做流式 Markdown**：当前 AFTER_LLM_CALL 事件携带完整 response 而非 chunk 流，流式渲染需要修改 Runtime 事件机制（新增 streaming 事件类型），超出本次 UI 优化范围。本次只优化视觉样式，不改事件架构
5. **Panel 宽度**：Rich Panel 默认自适应终端宽度，不需要手动计算
6. **向后兼容**：render_thinking/render_tool_call 的签名保持兼容（新增参数有默认值），不破坏现有调用

## Verification Steps

1. **运行测试**：`cd /Users/wanghanle/Documents/code/githubProject/Nexus && .venv/bin/python -m pytest tests/test_cli_display.py -v`
2. **运行所有测试**：`.venv/bin/python -m pytest tests/ -v` 确保无 regression
3. **手动验证 REPL**：
   ```bash   cd /Users/wanghanle/Documents/code/githubProject/Nexus && .venv/bin/python -m nexus.cli
   ```
   - 输入简单问题（如 "hello"）→ 验证用户消息绿色 Panel、AI 标签、最终回复 Markdown 正常显示
   - 输入需要工具的问题（如 "列出当前目录文件"）→ 验证思考链灰色 Panel、工具调用青色 Panel、最终回复正常
   - 多轮对话 → 验证分隔线清晰分隔每轮
   - 错误场景 → 验证错误红色 Panel
4. **手动验证单次模式**：`.venv/bin/python -m nexus.cli "列出当前目录"` → 验证样式一致
