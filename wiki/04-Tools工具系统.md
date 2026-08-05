# Tools 工具系统详解

Tools 是 Agent 与外部世界交互的桥梁。Nexus 的工具系统设计遵循"类优于函数"的哲学，通过统一接口让 LLM 能够自动发现、理解和调用工具。

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                       LLM (BaseLLM)                          │
│              通过 to_openai_schemas() 发现工具                │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    ToolRegistry (registry.py)                │
│              name → BaseTool 的 O(1) 索引中心                 │
│         注册 · 注销 · 查找 · 列表 · schema 导出               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                   ToolExecutor (executor.py)                 │
│           校验 → 执行（含超时） → 包装 → 记录                 │
│    统一参数校验 · 错误处理 · 超时控制 · 调用记录               │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                      BaseTool 实现层                          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────┐ │
│  │ Calculator │ │   Echo     │ │CurrentTime │ │  Shell    │ │
│  │  (内置)    │ │  (内置)    │ │  (内置)    │ │ (文件系统)│ │
│  └────────────┘ └────────────┘ └────────────┘ └───────────┘ │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐               │
│  │ ReadFile   │ │ WriteFile  │ │  ListDir   │ │ SearchContent│
│  │(文件系统)  │ │ (文件系统)  │ │ (文件系统) │ │  (文件系统)  │
│  └────────────┘ └────────────┘ └────────────┘               │
│  ┌─────────────────────────────────────────────┐            │
│  │         MCP Tools (mcp__/adapter.py)         │            │
│  │   通过 Model Context Protocol 接入远端工具    │            │
│  └─────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 基础抽象 (`nexus/tools/base.py`)

### 2.1 为什么用类而非函数？

代码中的注释给出了四个核心理由：

1. **内部状态管理** —— 工具可以有连接池、缓存、限流器等。函数只能闭包捕获外部状态，不利于资源生命周期管理
2. **继承与多态** —— 可定义工具族（如 `CrudTool` 基类），子类覆写部分行为
3. **统一元数据获取** —— `isinstance(obj, BaseTool)` 即可安全获取 name/description/schema
4. **可测试性** —— 可 mock 工具实例，替换 execute 方法进行单元测试

### 2.2 ToolResult —— 统一的结果包装

```python
@dataclass
class ToolResult:
    success: bool           # 执行是否成功
    data: Any = None        # 成功时的返回值
    error: str | None = None  # 失败时的错误描述（供 LLM 阅读纠错）
    tool_name: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0

    @classmethod
    def ok(cls, data, tool_name="", duration_ms=0.0): ...
    @classmethod
    def fail(cls, error, tool_name="", duration_ms=0.0): ...
```

**关键设计：成功/失败都返回 ToolResult，不抛出异常**

- LLM 作为调用方，通过阅读 `error` 文本自行纠正（如修正参数）
- 避免异常打断 Agent 循环，让 LLM 有机会重试
- `ToolResult.fail()` 的错误文本要写得像人话，LLM 才能理解

### 2.3 BaseTool 接口

```python
class BaseTool(ABC):
    @property @abstractmethod def name(self) -> str: ...          # 工具唯一名称
    @property @abstractmethod def description(self) -> str: ...   # 功能描述（LLM 阅读）
    @property @abstractmethod def schema(self) -> dict[str, Any]: ...  # JSON Schema 参数定义
    @abstractmethod async def execute(self, args: dict[str, Any]) -> ToolResult: ...

    def to_openai_schema(self) -> dict[str, Any]: ...  # 转换为 OpenAI function calling 格式
    @property def timeout(self) -> float | None: return None  # 可选超时覆盖
    async def setup(self) -> None: pass    # 可选初始化
    async def teardown(self) -> None: pass  # 可选清理
```

**生命周期设计：**
- `setup()` / `teardown()` 是可选的，不强制实现（很多工具无状态）
- ToolExecutor 在首次调用前检查并调用 `setup()`
- 有状态工具（如数据库连接）可在此打开/关闭连接

---

## 3. ToolRegistry —— 工具注册中心 (`nexus/tools/registry.py`)

### 3.1 职责

维护 `name → BaseTool` 的映射，提供 O(1) 查找性能（工具调用是热路径）。

### 3.2 核心方法

```python
class ToolRegistry:
    def register(self, tool: BaseTool) -> None:
        """注册工具。同名已存在时抛出 ValueError（拒绝静默覆盖）。"""

    def unregister(self, name: str) -> BaseTool | None:
        """按名称移除工具。返回被移除的实例（便于调用方清理）。"""

    def get(self, name: str) -> BaseTool | None:
        """按名称获取工具。"""

    def list(self) -> list[BaseTool]:
        """获取所有已注册工具（按插入顺序）。"""

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        """导出所有工具为 OpenAI function calling 格式。"""
```

**命名冲突策略：**
- 重复注册同名工具 → **抛出 ValueError**
- 原因：静默覆盖可能导致 Plugin 加载顺序影响行为，调试困难
- 若确实需要替换（如 Mock），先 `unregister()` 再 `register()`

**线程安全决策：**
- MVP 阶段为单线程 async 事件循环，暂未引入锁
- 工具注册通常发生在启动阶段（Plugin 加载），极少与运行时并发
- 未来如需并发，引入 `asyncio.Lock` 即可，接口无需变更

---

## 4. ToolExecutor —— 执行调度层 (`nexus/tools/executor.py`)

### 4.1 为什么需要 Executor？

在 BaseTool 和 Runtime 之间引入一个执行层，统一处理横切关注点：

```
校验 → 执行（含超时） → 包装 → 记录
```

1. **统一参数校验** —— 根据 JSON Schema 校验入参，避免 LLM 产生的畸形参数导致工具内部崩溃
2. **统一错误处理** —— 捕获所有异常并包装为 ToolResult
3. **调用记录与可观测性** —— 自动记录日志（工具名、call_id、耗时）
4. **超时控制** —— 通过 `asyncio.wait_for` 防止卡死工具阻塞 Agent
5. **扩展点** —— 未来可加入限流、重试、参数预处理

### 4.2 执行流程

```python
async def execute(self, tool_name, tool_call_id, arguments) -> ToolResult:
    # 1. 从 Registry 查找工具
    tool = self.registry.get(tool_name)
    if tool is None:
        return ToolResult.fail(error=f"Tool '{tool_name}' is not registered.")

    # 2. 校验参数（required / type）
    self._validate_args(tool, arguments)

    # 3. setup()（若存在）
    await tool.setup()

    # 4. 执行工具（含超时控制）
    # 超时优先级：tool.timeout > executor.default_timeout
    result = await asyncio.wait_for(tool.execute(arguments), timeout=effective_timeout)

    # 5. 包装结果（填充 tool_name / duration_ms）

    # 6. teardown()（若存在，失败不阻塞结果）
    await tool.teardown()

    return result
```

### 4.3 参数校验 (`_validate_args`)

MVP 阶段不引入 `jsonschema` 库，手动实现轻量校验：

1. 检查 `required` 列表中的字段是否存在
2. 检查每个已提供字段的类型是否与 schema 声明一致
3. 未知字段静默忽略（LLM 可能多传无关字段）

**类型映射表：**
```python
_SCHEMA_TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float),
    "boolean": bool, "array": list, "object": dict, "null": type(None),
}
```

---

## 5. 内置工具 (`nexus/tools/builtins.py`)

提供开箱即用的示例工具，覆盖不同场景：

| 工具 | 名称 | 用途 | 场景 |
|------|------|------|------|
| CalculatorTool | `calculator` | 安全数学表达式求值 | 测试工具系统是否正常 |
| EchoTool | `echo` | 原样返回输入 | 最简单的端到端测试 |
| CurrentTimeTool | `get_current_time` | 获取当前 UTC 时间 | 展示结构化数据返回 |

**CalculatorTool 的安全设计：**
- 使用受限命名空间执行 `eval()`（仅暴露 abs/round/min/max/pow/int/float）
- `__builtins__` 设为空字典，防止任意代码执行
- 生产环境应替换为沙箱化计算引擎

---

## 6. 文件系统工具 (`nexus/tools/file_tools.py`)

CLI Agent 的核心价值工具，让 Agent 能直接操作项目文件。

### 6.1 安全设计

所有文件系统工具共享以下安全机制：

1. **目录遍历防护** (`_resolve_path`)
   ```python
   base_real = os.path.realpath(base_dir)
   resolved_real = os.path.realpath(resolved)
   if os.path.commonpath([base_real, resolved_real]) != base_real:
       raise ValueError("Access denied: path is outside working directory.")
   ```
   - 使用 `os.path.commonpath` 而非 `startswith`，防止 `/etc/base` 绕过 `/etc` 前缀

2. **大小限制** — ReadFileTool / SearchContentTool 跳过超过 2MB 的文件
3. **行数限制** — ReadFileTool 默认最多返回 500 行
4. **递归深度限制** — ListDirTool 默认 max_depth=3
5. **条目数量限制** — ListDirTool 单次最多 500 条

### 6.2 工具列表

| 工具 | 名称 | 核心能力 |
|------|------|----------|
| ReadFileTool | `read_file` | 读取文件（支持行范围、多编码检测） |
| WriteFileTool | `write_file` | 创建/覆盖文件（自动创建父目录） |
| ListDirTool | `list_dir` | 列出目录（支持递归、跳过缓存目录） |
| SearchContentTool | `search_content` | 正则搜索文件内容（类似 grep） |

### 6.3 ReadFileTool 的实现亮点

```python
# 多编码尝试（优先 UTF-8，失败时 fallback）
_ENCODINGS = ("utf-8", "gbk", "latin-1", "cp1252")

# 带行号的输出格式
"  42 | def hello_world():"
```

---

## 7. Shell 工具 (`nexus/tools/shell_tool.py`)

```python
class ShellTool(BaseTool):
    """执行 Shell 命令。"""
```

**安全设计：**
- 目录遍历防护（与文件工具一致）
- 超时控制（默认 30 秒）
- 工作目录限定（防止命令在任意目录执行）
- 危险命令过滤（可选，MVP 阶段未实现）

---

## 8. MCP 工具接入 (`nexus/tools/mcp/`)

MCP（Model Context Protocol）是 Anthropic 推出的开放协议，允许 Agent 通过标准接口接入外部工具服务。

### 8.1 架构

```
Nexus Agent
    │
    ▼
MCPPlugin (nexus/tools/mcp/plugin.py)
    │
    ├── MCPManager (nexus/tools/mcp/manager.py)
    │       ├── 维护多个 MCPClient（按 server name）
    │       └── 提供 connect / disconnect / reconnect / reload
    │
    └── MCPAdapter (nexus/tools/mcp/adapter.py)
            └── 将 MCP Tool 转换为 Nexus BaseTool
```

### 8.2 命名约定

MCP 工具在注册到 ToolRegistry 时使用 `mcp__{server}__{tool}` 格式的名称：
- 例：`mcp__filesystem__read_file`
- 前端通过 `name.split("__")` 提取来源 server

### 8.3 传输模式

支持两种 MCP 传输：
- **stdio**：本地子进程（command + args + env）
- **http**：远程服务（url）

---

## 9. 工具与 Agent 的协作流程

```
1. Agent.run(task)
   ├── 2. Runtime 调用 policy.next_action(context)
   │      └── ReActPolicy 构建 LLM 请求，附带 tools schemas
   ├── 3. LLM 返回 tool_calls
   │      └── [ToolCall(id="call_1", name="read_file", arguments={"path": "main.py"})]
   ├── 4. Runtime 创建 ToolCallAction
   ├── 5. Runtime.execute_tool_call()
   │      ├── ToolExecutor.execute("read_file", "call_1", {"path": "main.py"})
   │      ├── Registry.get("read_file") → ReadFileTool
   │      ├── _validate_args() → 通过
   │      ├── ReadFileTool.execute({"path": "main.py"})
   │      └── 返回 ToolResult(success=True, data="...")
   ├── 6. 将 tool result 追加到 state.messages
   ├── 7. 派发 AFTER_TOOL_CALL 事件
   └── 8. 下一轮循环：policy 看到 tool result，决定下一步
```

---

## 10. 开发新工具的步骤

1. **继承 BaseTool**
   ```python
   class MyTool(BaseTool):
       @property
       def name(self) -> str: return "my_tool"
       @property
       def description(self) -> str: return "工具功能描述..."
       @property
       def schema(self) -> dict: return {"type": "object", "properties": {...}}
       async def execute(self, args) -> ToolResult:
           # 实现工具逻辑
           return ToolResult.ok(data=result)
   ```

2. **注册到 Agent**
   ```python
   agent.register_tool(MyTool())
   ```

3. **或通过工厂批量注册**（内置工具）
   - 修改 `nexus/core/factory.py` 的 `register_tools()`
   - 将工具加入 `all_tools` 字典

4. **编写测试**
   - 测试 execute() 的核心逻辑
   - 测试参数校验边界
   - 测试错误返回（ToolResult.fail）

---

## 11. 关键设计决策

### 11.1 为什么 ToolResult 包含 error 文本而非异常？

- LLM 是调用方，它无法"捕获异常"
- 通过阅读 error 文本，LLM 可以自行纠正参数并重新调用
- 例如：error="File not found: main.py" → LLM 可能改为 read_file("src/main.py")

### 11.2 为什么 schema 用 JSON Schema 而非 Pydantic？

- LLM 的 function calling 接口本身就使用 JSON Schema
- 减少转换层：直接定义 schema，无需 Pydantic model → JSON Schema 的转换
- 框架零依赖（MVP 阶段不引入 jsonschema 库做校验）

### 11.3 为什么工具超时在 Executor 层而非 BaseTool？

- 统一控制：所有工具共享默认超时，单个工具可覆写
- 横切关注点：超时策略是执行策略的一部分，不应耦合到工具实现
- 便于未来扩展：可在 Executor 层实现更复杂的超时策略（如分级超时）
