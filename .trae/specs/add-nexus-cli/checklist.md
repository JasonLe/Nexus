# Checklist

## 基础设施

- [x] `pyproject.toml` 含 `[project.scripts]` 入口点 `nexus`
- [x] `pyproject.toml` 含 `[cli]` 可选依赖（rich, prompt-toolkit）
- [x] `nexus/__main__.py` 支持 `python -m nexus` 启动
- [x] `nexus` 命令在 pip install -e 后可在 PATH 中使用

## 配置管理

- [x] `CLIConfig` dataclass 包含所有必要字段（model/provider/api_key/base_url/system_prompt/max_steps/work_dir）
- [x] 命令行参数可覆盖环境变量和配置文件
- [x] 环境变量（NEXUS_MODEL/NEXUS_API_KEY/NEXUS_BASE_URL）自动读取
- [x] `~/.nexus/config.json` 和 `.nexus.json` 正确合并，项目级优先
- [x] 配置文件不存在时使用合理默认值不报错

## 终端显示

- [x] DisplayManager 支持 Markdown 渲染
- [x] 流式内容追加无闪烁
- [x] 工具调用以 Panel/Tree 组件展示（含 spinner）
- [x] 错误信息以红色 Panel 展示
- [x] 执行摘要（步骤数/Token 数/耗时）正确显示
- [x] `--verbose` / `--debug` 输出对应级别日志

## CLI 文件工具

- [x] ReadFileTool 支持读取文件（含行号，默认 500 行限制）
- [x] WriteFileTool 支持写入文件（含覆盖确认）
- [x] ListDirTool 支持列出目录（含递归深度控制）
- [x] SearchContentTool 支持文件内容搜索（grep）
- [x] 工具正确注册到 ToolRegistry

## 会话管理

- [x] 退出时自动保存会话到 `~/.nexus/sessions/`
- [x] `--continue` 恢复最近会话
- [x] `--list-sessions` 列出历史会话
- [x] 会话文件为 JSON 格式，含 messages/steps/元数据
- [x] 过长历史自动截断

## CLI 主流程

- [x] `nexus "prompt"` 直接执行模式正常
- [x] `nexus` 无参数进入 REPL
- [x] argparse 参数解析涵盖所有配置项
- [x] StreamHandler 正确将流式 chunk 转发给 DisplayManager

## REPL

- [x] 多轮对话保持上下文
- [x] `quit`/`exit`/Ctrl+D 退出 REPL
- [x] 内置命令 `/clear`、`/save`、`/tools`、`/quit` 可用
- [x] Ctrl+C 中断当前任务但不退出 REPL
- [x] prompt_toolkit 多行输入正常
- [x] 历史记录持久化（FileHistory）

## 测试

- [x] test_cli_config.py 覆盖三级加载和合并逻辑
- [x] test_cli_display.py 覆盖各渲染组件
- [x] test_cli_tools.py 覆盖文件工具读写操作
- [x] test_cli_session.py 覆盖保存/加载/列表

## 代码质量

- [x] 每个 CLI 模块文件有模块级 docstring
- [x] 每个类/方法有 docstring 说明职责
- [x] 关键流程有行内注释
- [x] 类型提示完整

## 文档

- [x] README 新增 CLI 章节（安装/快速开始/命令参考）
