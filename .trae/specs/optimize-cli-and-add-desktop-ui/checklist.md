# Checklist

- [x] CLI：用户输入以绿色 `👤 You` Panel 回显，不再消失在 prompt 行（repl.py/main.py 已启用，test_cli_display 29 项通过）
- [x] CLI：Thinking 流式块带 dim 左侧竖线 gutter，与 AI 回复明确区分，且保持纯 append 滚动安全（`print_thinking_chunk` 行首状态机，含跨 chunk 测试）
- [x] CLI：`/tools` 以 Rich Table 渲染（名称/描述/参数），`/help` 以 Rich Panel 渲染，无裸 print
- [x] CLI：工具调用为紧凑 Panel（成功青色/失败红色），`_run_single` 与 REPL 样式一致
- [x] Server：`nexus serve` 可启动，`GET/PUT /api/config` 读写 nexus.yaml 生效（文件权限 0600，冒烟验证脱敏正确）
- [x] Server：`/api/sessions` 列表/详情/删除与 `/api/tools` 正常返回（test_server_api 覆盖）
- [x] Server：`/ws/chat` 推送 thinking_delta/content_delta/tool_call/done/error 事件序列正确，多轮上下文保持（10 项测试，含并发拒绝与 reset/restore）
- [x] Server：`tests/test_server_api.py` 通过，CLI 既有测试无回归（全量 274 passed，仅 3 个 Windows 既有失败）
- [x] Desktop：深色数据艺术风 + 全中文界面，前端 build 无错误（tsc + vite 零错误）
- [x] Desktop：会话页流式渲染 Thinking（可折叠）、Markdown 回复、工具调用卡片，多轮对话正常（浏览器冒烟 7/7：真实模型两轮对话上下文正确）
- [x] Desktop：会话列表/恢复/删除/新建可用（浏览器验证；并修复了 WS 会话未持久化问题，done 事件现携带 session_id 覆盖式落盘）
- [x] Desktop：配置页可编辑 providers/默认 provider/agent/工具/流式开关并保存写回 nexus.yaml，新会话生效（PUT 单测覆盖 + UI 渲染验证）
- [x] Desktop：工具页展示已注册工具及 schema（5 个工具卡片参数表完整）
- [x] Desktop：仅通过 HTTP/WebSocket 与后端交互，未复制或修改 nexus/core 决策逻辑
- [x] Electron：主进程可拉起/附着后端，窗口正常打开并完成一次对话（SMOKE_OK，复用已有后端实例验证通过）
- [x] `nexus ui` 浏览器模式可用（curl 根路径返回前端 index.html）
- [x] 端到端冒烟测试通过（pytest 全量 + 浏览器 UI 冒烟 + Electron 冒烟）

## 已知非阻塞观察项
- MiniMax 流式端点未返回 usage 统计时，UI 的 token 用量显示为 0（provider 行为，非 UI/服务缺陷）
- Electron 安装包不含 Python 后端，目标机器需预装 `nexus` CLI（desktop/README.md 已注明）
