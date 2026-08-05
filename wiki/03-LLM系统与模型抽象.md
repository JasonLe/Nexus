# LLM 系统与模型抽象详解

LLM（大语言模型）层是 Nexus 框架与各种模型提供商之间的适配层。它的核心设计目标是**模型无关性**——通过统一的抽象接口，让上层 Core 代码无需关心底层是 OpenAI、Anthropic 还是其他兼容模型。

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        应用层 (Agent/CLI/Server)               │
│                   只依赖 BaseLLM 抽象接口                      │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                      LLM 抽象层 (nexus/llm/)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ BaseLLM  │  │LLMResponse│  │ LLMChunk │  │  UsageStats  │ │
│  │ (base.py)│  │(base.py) │  │(base.py) │  │  (base.py)   │ │
│  └────┬─────┘  └──────────┘  └──────────┘  └──────────────┘ │
│       │                                                      │
│  ┌────┴──────────────────────────────────────────────────┐   │
│  │              TokenCounter + _retry                     │   │
│  │         (token_counter.py / _retry.py)                 │   │
│  └────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                     Provider 实现层                           │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐ │
│  │ OpenAILLM  │  │AnthropicLLM│  │   MiniMaxAnthropicLLM  │ │
│  │(openai.py) │  │(anthropic. │  │      (minimax.py)      │ │
│  └────────────┘  └────────────┘  └────────────────────────┘ │
│       │                │                    │                │
│       ▼                ▼                    ▼                │
│   openai SDK      anthropic SDK      anthropic SDK           │
│   (Chat Completions) (Messages API)  (兼容 Anthropic 格式)   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 核心数据结构 (`nexus/llm/base.py`)

### 2.1 UsageStats —— Token 使用统计

```python
@dataclass
class UsageStats:
    prompt_tokens: int = 0      # 输入消耗的 token 数
    completion_tokens: int = 0  # 输出消耗的 token 数
    total_tokens: int = 0       # 总和
```

这是一个纯数据类，用于聚合一次 LLM 调用的 token 消耗。所有 Provider 在返回 `LLMResponse` 时都需要构造此对象。

### 2.2 ToolCall —— LLM 返回的工具调用

```python
@dataclass
class ToolCall:
    id: str                      # 工具调用唯一标识（用于后续 tool result 回传匹配）
    name: str                    # 工具名称
    arguments: dict[str, Any] = field(default_factory=dict)  # 调用参数
```

当 LLM 决定调用工具时，会返回一个或多个 `ToolCall`。`id` 字段至关重要——后续 tool result 消息必须携带对应的 `tool_call_id`，才能让 LLM 知道是哪个工具调用的结果。

### 2.3 LLMResponse —— 完整响应

```python
@dataclass
class LLMResponse:
    content: str = ""            # 模型生成的文本内容
    reasoning_content: str = ""  # 思考链内容（Claude extended thinking / MiniMax adaptive thinking）
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: UsageStats = field(default_factory=UsageStats)
    model: str = ""              # 实际使用的模型名称
    finish_reason: str = ""      # 结束原因: "stop" | "tool_calls" | "length" | "content_filter"
    raw_response: Any = None     # 原始 provider 响应（调试用）
```

**设计决策：为什么需要 `reasoning_content`？**

传统 LLM 只有 content，但 Claude extended thinking 和 OpenAI o1/o3 系列会输出独立的 thinking/reasoning 内容。将这些内容分离出来：
- 下游可以选择性展示（如折叠思考过程）
- 避免 thinking 内容污染正式的 assistant 消息
- 便于统计和审计

### 2.4 LLMChunk —— 流式数据块

```python
@dataclass
class LLMChunk:
    delta_content: str = ""       # 增量文本内容
    delta_reasoning: str = ""     # 增量思考链内容
    delta_tool_calls: list[ToolCall] = field(default_factory=list)  # 当前累积的 tool_calls
    finish_reason: str | None = None  # 流结束时的结束原因
```

**关键设计：增量语义**

- `delta_content` 是**增量文本**（本事件的增量），下游按增量拼接
- `delta_tool_calls` 是**当前累积状态**（非增量），因为 tool call 的 arguments 是 JSON 片段，无法安全地增量解析

---

## 3. BaseLLM 抽象基类

### 3.1 接口定义

```python
class BaseLLM(ABC):
    def __init__(self) -> None:
        self.context_window_tokens: int = 0  # 0 = 不启用自动截断

    @abstractmethod
    async def chat(self, messages, tools=None, **kwargs) -> LLMResponse: ...

    @abstractmethod
    async def stream_chat(self, messages, tools=None, **kwargs) -> AsyncIterator[LLMChunk]: ...
```

### 3.2 Context Window 防护

```python
def _maybe_truncate_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if self.context_window_tokens <= 0:
        return messages
    budget = int(self.context_window_tokens * 0.75)  # 预留 1/4 给输出
    from nexus.llm.token_counter import truncate_messages_to_fit
    return truncate_messages_to_fit(messages, max_tokens=budget, model=self.model)
```

**设计思路：**
- 子类在构造函数中设置 `context_window_tokens`
- 发送前自动截断，保留 system 消息和最近若干轮对话
- 预留 25% 空间给模型输出，避免输入占满导致输出被截断

### 3.3 扩展约定

| 维度 | 要求 |
|------|------|
| chat() | 返回完整的 LLMResponse（含 content / tool_calls / usage） |
| stream_chat() | 返回 AsyncIterator[LLMChunk]，每个 chunk 含增量内容 |
| tools 参数 | OpenAI 兼容格式的 tool schema list |
| 不支持 tool calling | 忽略 tools 参数，返回 `tool_calls=[]` |
| 实现简化 | chat() 可复用 stream_chat() 遍历聚合 |

---

## 4. Provider 实现

### 4.1 OpenAILLM (`nexus/llm/providers/openai.py`)

**基于 `openai.AsyncOpenAI` SDK**，对接 OpenAI Chat Completions API。

**关键实现细节：**

1. **错误分类**：将 OpenAI SDK 异常按 HTTP 状态码分类为可重试/不可重试
   - 401/403 → LLMAuthError（不可重试）
   - 429 → LLMRateLimitError（可重试）
   - 5xx → LLMServerError（可重试）
   - 超时 → LLMTimeoutError（可重试）

2. **流式 tool calling 聚合**：
   ```python
   pending_tool_calls: dict[int, dict[str, str]] = {}
   # 按 tool_call.index 聚合 delta
   # 同一个 tool_call 可能由多个 chunk 共同构成
   ```

3. **reasoning_content 支持**：检测 `delta.reasoning_content` 字段（OpenAI o1/o3 系列）

### 4.2 AnthropicLLM (`nexus/llm/providers/anthropic.py`)

**基于 `anthropic.AsyncAnthropic` SDK**，对接 Anthropic Messages API。

**此类是 Anthropic 生态的基类**——任何使用 Anthropic API 格式的 provider（如 MiniMax）只需继承此类并覆写构造函数默认值。

**核心格式转换：**

```
Nexus (OpenAI 格式)              Anthropic 格式
─────────────────────            ─────────────────
system role 消息              →  system 参数（独立）
assistant tool_calls            →  content 中的 tool_use block
role="tool" 消息               →  role="user" + tool_result block
```

**流式事件处理：**

Anthropic SDK 的 `messages.stream()` 产生不同 event types：
- `content_block_start` — tool_use block 开始，记录 id/name
- `content_block_delta` — text_delta / thinking_delta / input_json_delta
- `content_block_stop` — block 结束，解析完整 tool_call
- `message_stop` — 消息结束，获取 stop_reason

### 4.3 MiniMaxAnthropicLLM (`nexus/llm/providers/minimax.py`)

继承自 `AnthropicLLM`，覆写默认值：
- `_default_model = "MiniMax-Text-01"`
- `_default_base_url = "https://api.minimaxi.com/anthropic"`
- `_default_thinking = {"type": "adaptive"}`  # 启用 adaptive thinking

**设计优势**：通过继承复用 Anthropic 格式转换和流式处理逻辑，只需修改配置默认值。

---

## 5. 重试机制 (`nexus/llm/_retry.py`)

```python
async def with_retry(coro_fn, max_retries=3, operation_name=""):
    """指数退避重试，仅对 LLMRetryableError 子类重试。"""
```

**重试策略：**
- 仅重试可重试错误（LLMRetryableError 子类）：429/5xx/超时
- 不重试不可重试错误（LLMAuthError/LLMError）：401/403/其他
- 指数退避：间隔时间随重试次数增长
- 最大重试次数：默认 3 次

**设计决策：为什么不在 BaseLLM 中统一实现重试？**

- 重试策略可能因 provider 而异（如 Anthropic 有官方重试建议）
- 保持 BaseLLM 接口纯净，重试作为横切关注点由调用方或 wrapper 处理
- 当前实现中，各 provider 在 chat()/stream_chat() 内部调用 with_retry

---

## 6. Token 计数与截断 (`nexus/llm/token_counter.py`)

```python
def truncate_messages_to_fit(messages, max_tokens, model="gpt-4"):
    """保留 system 消息 + 最近 N 轮对话，删除最早历史。"""
```

**实现策略：**
1. 优先使用 `tiktoken` 精确计数
2. tiktoken 不可用时（模型不支持或库未安装），使用字符近似：`chars // 4`
3. 截断时保留所有 system 消息（提示词模板不能丢）
4. 非 system 消息按轮次（user + assistant）保留最近部分

---

## 7. 异常体系 (`nexus/core/exceptions/__init__.py`)

LLM 层使用 Core 中定义的异常体系：

```
LLMError (基类)
├── LLMRetryableError (可重试)
│   ├── LLMRateLimitError  (429)
│   ├── LLMServerError     (5xx)
│   └── LLMTimeoutError    (超时)
└── LLMAuthError           (401/403，不可重试)
```

**分类意义**：Runtime 可根据异常类型决定是否继续执行（可重试错误可能自动恢复，鉴权错误必须人工介入）。

---

## 8. 添加新 Provider 的步骤

要支持一个新的 LLM 提供商（如 Google Gemini、本地 vLLM）：

1. **确认 API 格式**：是否兼容 OpenAI 或 Anthropic 格式？
   - 兼容 → 继承现有基类，覆写默认值（如 MiniMax）
   - 不兼容 → 继承 BaseLLM，实现 chat() 和 stream_chat()

2. **实现格式转换**：将 Nexus 统一格式 ↔ Provider 原生格式

3. **错误映射**：将 Provider SDK 异常转换为 LLMError 子类

4. **注册到工厂**：在 `nexus/core/factory.py` 的 `create_llm()` 中添加分支

5. **添加到配置**：在 `nexus/cli/config.py` 的 `_default_providers()` 中添加默认配置

---

## 9. 关键设计决策

### 9.1 为什么用类而非函数定义 Provider？

- **状态管理**：每个 Provider 实例持有独立的 SDK 客户端（连接池、超时配置）
- **多实例并存**：可同时创建 OpenAI + Anthropic 两个实例，按需切换
- **配置隔离**：不同实例可有不同 model/api_key/base_url

### 9.2 为什么 messages 用 OpenAI 格式作为内部标准？

- OpenAI 格式是行业事实标准，大多数 provider 都兼容
- 减少内部转换次数：AgentState → OpenAI 格式 → Provider 格式
- 便于序列化保存到会话文件

### 9.3 为什么 stream_chat() 不返回累积内容？

- `delta_content` 是增量文本，下游（CLI UI / WebSocket）按增量拼接
- 避免重复传输：如果返回累积全文，每次都要传输越来越长的字符串
- 但 `delta_tool_calls` 是累积状态，因为 JSON 片段无法安全增量解析
