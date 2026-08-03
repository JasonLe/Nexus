# Nexus 项目迭代计划

> 版本：v1.0（2026-08）
> 范围：对标业界主流 AI Agent 工具（Claude Code / Codex / opencode / Aider / LangGraph / AutoGen / CrewAI / Gemini CLI / Cursor）后的差距补齐与差异化建设

---

## 一、现状评估

### 已有能力

| 维度 | 现状 | 业界对位 |
|------|------|----------|
| 核心架构 | Runtime/Policy 分离 + Action 调度 | ≈ AutoGen Core 分层、LangGraph executor 解耦 |
| 事件系统 | EventBus 8 种事件，handler 异常隔离 | ≈ AutoGen 事件驱动；天然的可观测性埋点 |
| 扩展机制 | Plugin 生命周期（install/activate/deactivate） | 对标 Claude Code / Gemini CLI 扩展体系的基础 |
| 模型层 | OpenAI / Anthropic / MiniMax 三 Provider + 重试 + 流式 | 满足模型无关标配 |
| 工具系统 | BaseTool 抽象 + @tool 装饰器 + ToolRegistry + 文件/shell 内置工具 | 基础完备 |
| 交互层 | CLI REPL（Rich 美化）+ FastAPI Server + WebSocket + Electron 桌面端 | 最接近 opencode 的 client/server 架构 |
| 会话管理 | JSON 持久化 + 恢复 + 删除 + 覆盖式保存 | 满足会话持久化标配 |
| 配置体系 | 五级合并（CLI > env > 项目级 > 用户级 > 默认） | 基础完备 |

### 架构理念判断

Nexus 的「Agent 操作系统内核」定位——Core 只做调度、一切高级能力可插拔——在理念上与 AutoGen Core、LangGraph runtime 属于同代设计。**当前差距不在架构，而在生态接口（MCP）、安全面（审批+沙箱）、记忆落地三个标配项**，以及两个预留但空置的差异化抓手（`memory/implementations/`、`workflow/`）。

---

## 二、对标差距矩阵

图例：● 完整　◐ 部分　— 缺失

| 能力维度 | Nexus | 业界水位 | 差距性质 |
|----------|:-----:|:--------:|----------|
| Agent 工具调用循环（ReAct） | ● | 标配 | — |
| 多 Provider 切换 | ● | 标配 | — |
| 会话持久化/恢复 | ● | 标配 | — |
| 工具输出截断 | ● | 标配 | — |
| Server/Desktop 部署形态 | ● | 差异化 | 领先 |
| **MCP client** | — | 标配（除 Aider 外全员具备） | **P0 生态缺口** |
| **审批 / HITL 权限体系** | ◐（仅雏形） | 标配 | **P0 安全缺口** |
| **记忆系统落地** | ◐（仅抽象层） | 标配 | **P0 能力缺口** |
| 指令文件（AGENTS.md 类） | — | 标配 | P1 |
| 上下文 LLM 摘要压缩（auto-compact） | —（仅截断） | 标配 | P1 |
| headless 模式（CI 嵌入） | ◐（单 prompt 模式） | 标配 | P1 |
| 可观测性（tracing / 回放） | ◐（有事件总线未导出） | 差异化 | P1（边际成本低） |
| Shell 沙箱 / 命令 allowlist | — | 差异化（Codex 标杆） | P2 |
| 工作流编排（DAG/状态机） | —（目录空置） | 差异化 | P2 |
| Subagent / 多 Agent | — | 差异化 | P2 |
| 代码库索引（repo map 类） | — | 差异化（Aider/Cursor） | P3 |
| 云端异步 Agent | — | 差异化 | P3 |

---

## 三、迭代原则

1. **Core 稳定不动**：所有新能力通过既有扩展点接入——Policy 扩展、Plugin 注入、Tool 注册、EventBus 订阅、BaseMemory/BaseLLM 实现。Runtime 调度循环零修改或最小修改。
2. **标配先行，差异化殿后**：先补齐 MCP / 审批 / 记忆三个入场券能力，再做 workflow 编排与可观测性两个差异化抓手。
3. **架构一致性复用**：审批即一种 Policy 行为、记忆即 BaseMemory 实现、tracing 即 EventBus 订阅者、MCP 工具即 BaseTool 适配——不为新能力引入第二套机制。
4. **小步可验证**：每个迭代项独立交付、独立测试，CLI/Desktop 双端同步受益。
5. **Desktop 跟随 Server 演进**：Server 暴露的能力（新 REST/WS 事件）同步在 Desktop 呈现，保持 client/server 解耦。

---

## 四、迭代路线图

### Milestone 1：生态与安全标配（P0）

> 目标：补齐三个入场券能力。完成后 Nexus 具备与主流 coding agent 同等的基础信任度。

#### 1.1 MCP Client 支持

业界事实标准的工具协议，一次接入即获得全部 MCP 生态工具（filesystem/git/github/postgres/puppeteer 等数千个 server）。

- 在 `nexus/tools/` 新增 `mcp/` 子包：`MCPClient`（stdio + Streamable HTTP 传输）、`MCPToolAdapter`（把 remote MCP tool 适配为 BaseTool，schema 透传，结果包装为 ToolResult）
- 配置层：`nexus.yaml` 新增 `mcp_servers` 节（name/command/args/env/url），CLI 与 Desktop 配置页均可编辑
- 接入方式：实现为一个官方 Plugin（`MCPPlugin`）——install 阶段连接各 server、拉取工具列表、注册适配器；deactivate 阶段断开。**不修改 Runtime**
- Desktop：工具页区分「内置工具 / MCP 工具」分组展示
- 验证：接入官方 filesystem MCP server 与 memory MCP server 端到端跑通

#### 1.2 审批 / HITL 权限体系

- **权限模式**：新增 `--mode`（plan / default / auto）与配置项 `permission.mode`。
  - `plan`：只读模式——Policy 层过滤掉写类工具（write_file/shell），只生成计划
  - `default`：写操作需用户确认
  - `auto`：全部自动执行
- **实现路径**：不修改 Runtime——通过「包装 ToolExecutor 的审批插件」实现：审批插件订阅 `BEFORE_TOOL_CALL`，对需确认的工具触发审批请求；CLI 端用 prompt_toolkit 弹确认（y/n/always）；Server 端经 WS 推送 `approval_request` 事件，Desktop 渲染审批卡片，用户点击后经 WS 回传 `approval_response`
- **细粒度规则**：配置 `permission.allow` / `permission.deny`（per-tool、shell 命令正则），命中 allow 跳过确认
- 这是三个 P0 项中唯一需要给 EventBus 增加「可中断」语义的工作：审批 handler 需要阻塞 ToolCallAction 的执行直至用户响应（在插件内用 asyncio.Event 等待即可，Runtime 无需感知）

#### 1.3 记忆系统落地

激活空置的 `nexus/memory/implementations/`：

- **短期记忆** `ShortTermMemory`：进程内 ring buffer，接入 Agent.run()——run 开始时写入用户输入、结束时写入结果；配合上下文压缩（M2）使用
- **长期记忆** `FileMemory`：零依赖起步，JSONL 存储于 `~/.nexus/memory/`，支持 add/search（关键词）/list；接口预留 embedding 参数，后续可换向量实现
- **记忆工具**：注册 `memory_save` / `memory_search` 两个内置工具，让 LLM 自主存取（对标 Gemini CLI 的 save_memory）
- **指令文件**：加载项目根 `NEXUS.md`（兼容识别 `AGENTS.md`），内容注入 system prompt——这是业界成本最低收益最高的标配
- Desktop：新增「记忆」视图（列表/搜索/删除记忆条目）

### Milestone 2：上下文与工程效率（P1）

> 目标：长任务不炸上下文、CI 可嵌入、执行过程可观测。

#### 2.1 上下文智能压缩（auto-compact）

- 新增 `CompactionPolicy` 装饰器或在 ReActPolicy 内实现：每次 LLM 调用前用 token_counter 估算，超过 context_window 阈值（默认 80%）时，先调用一次 LLM 把早期 messages 摘要为一条 summary 消息，替换原始历史（保留 system + 摘要 + 最近 N 轮）
- Desktop 显示「上下文已压缩」事件（新增 EventType 或复用现有事件 payload）
- CLI 新增 `/compact` 手动压缩与 `/context` 查看占用

#### 2.2 headless / CI 模式

- `nexus run "task" --json`：结构化输出（结果/steps/token 用量/工具调用），供脚本消费
- `--output-format json|text`、`--no-stream`、`--timeout`
- 退出码语义化：0 成功 / 1 错误 / 2 超步数

#### 2.3 可观测性：Tracing 与回放

- 实现 `TracingPlugin`：订阅 EventBus 全部事件，按 run_id 聚合为 trace JSON（spans：llm_call / tool_call，含输入输出、耗时、token），落盘 `~/.nexus/traces/`
- 导出接口：OTLP（OpenTelemetry）可选 extra，接 Jaeger/Langfuse
- **回放**：`nexus trace replay <run_id>` 从 trace 重建执行序列（配合 Action 序列化，为既有设计目标的兑现）
- Desktop：新增「运行轨迹」视图——时间轴展示一次任务的 LLM/工具调用序列与耗时（直接消费 trace JSON）

### Milestone 3：差异化能力（P2）

> 目标：激活空置的 `workflow/`，建立编排与安全两个护城河。

#### 3.1 Workflow 编排

在 `nexus/workflow/` 实现声明式工作流，与 Agent 循环互补（Agent 处理开放任务，Workflow 处理结构化流程）：

- `WorkflowDefinition`：YAML/JSON 声明节点（llm 节点 / tool 节点 / agent 子运行节点 / condition 分支 / parallel 并行）与边
- `WorkflowRunner`：本质是**一种特殊 Policy**——把 DAG 执行映射为 Action 序列交给 Runtime 调度，完全复用现有调度循环、事件、日志，不引入第二套执行引擎
- 状态持久化复用 SessionManager；节点级失败重试策略
- Desktop：后续可加只读 DAG 可视化（先不做编辑器）

#### 3.2 Shell 执行安全层

- `ShellTool` 增加 allowlist/denylist 配置（命令正则），默认拒绝危险命令（rm -rf /、format 等）
- 可选 Docker 沙箱执行器（`SandboxedShellTool`，继承 BaseTool 替换执行后端），镜像内挂载 work_dir 只读/读写
- 与 M1.2 审批体系联动：越权命令触发审批而非静默拒绝

#### 3.3 Subagent

- `SubagentTool`：一个特殊工具，内部创建独立 Agent 实例（独立 AgentState、独立上下文窗口、可配置工具子集）执行子任务，结果回传主 Agent
- 配置：subagent 的 system_prompt / 可用工具 / max_steps 在 YAML 声明
- 利用现有 Agent 门面即可实现，无需 Runtime 改动

### Milestone 4：生态深化（P3，按需启动）

- **代码库索引工具**：tree-sitter 解析 + 轻量排序（类 Aider repo map），注册为 `codebase_map` 工具
- **插件市场**：插件以 entry_points 注册，`nexus plugins list/install`；官方维护插件索引
- **更多 Provider**：Gemini、DeepSeek、Ollama（本地模型）
- **云端任务**：Server 支持后台队列执行长任务（依赖 M3.1 的 durable 能力）

---

## 五、优先级与依赖

```
M1.1 MCP ─────────────┐
M1.2 审批/HITL ────────┼── 并行，互不依赖；M1.2 是 M3.2 的前置
M1.3 记忆 ─────────────┘
        ↓
M2.1 上下文压缩（依赖 M1.3 的短期记忆接入点）
M2.2 headless（独立）
M2.3 Tracing（独立，建议早做——后续所有迭代的调试都受益）
        ↓
M3.1 Workflow（依赖 M2.3 trace 能力做节点观测）
M3.2 Shell 沙箱（依赖 M1.2 审批联动）
M3.3 Subagent（独立）
        ↓
M4 按需
```

**建议的近期三个迭代**：M1.1（MCP）→ M1.2（审批）→ M2.3（Tracing）。理由：MCP 是生态杠杆最大的一项；审批是桌面端用户信任的前提；Tracing 为后续所有复杂迭代提供调试与验证基础设施。

---

## 六、架构约束（所有迭代必须遵守）

1. 新能力优先以 **Plugin / Tool / Policy / BaseMemory / BaseLLM** 五种扩展点之一接入；禁止在 Runtime 主循环中为新功能加分支。
2. Desktop 只通过 HTTP/WebSocket 与后端交互；任何新桌面功能必须先在 Server 层暴露契约。
3. 配置一律走五级合并体系，新增配置项同步更新 `generate_config_template()` 与 Desktop 配置页。
4. 每个迭代项交付标准：实现 + pytest 用例 + CLI 验证 +（如涉及 UI）Desktop 冒烟。
5. 事件类型新增需向后兼容（追加而非修改 payload 字段）。
