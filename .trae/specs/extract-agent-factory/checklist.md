# Agent 工厂重构 —— 检查点验证结果

验证时间:2026-08-04
验证方式:Read 代码 + Grep import 关系 + pytest 运行

## 检查点

- [x] `nexus/core/factory.py` 存在并导出 `create_agent(config) -> Agent`
  - 证据:factory.py 第 119-145 行定义 `create_agent(config) -> Agent`,文件顶部 docstring 说明三函数公开接口。

- [x] `create_agent` 内部封装 LLM 创建 + Agent 构造 + 工具注册三步
  - 证据:factory.py 第 137-144 行依次调用 `create_llm(config)` → `Agent(...)` → `register_tools(agent, config)`,与 docstring 描述一致。

- [x] `create_agent` 支持三个 provider 分支:openai / anthropic / minimax
  - 证据:`create_llm`(factory.py 第 55-78 行)按 `provider.lower()` 分支: `minimax` → MiniMaxAnthropicLLM; `anthropic` → AnthropicLLM; `else` → OpenAILLM。

- [x] `create_agent` 工具注册逻辑:enabled=[] 时注册全部,非空时只注册列表内工具
  - 证据:`register_tools`(factory.py 第 109 行)`enabled = set(config.tools.enabled) if config.tools.enabled else set(all_tools.keys())`,空列表走全量,非空走过滤。

- [x] `nexus/cli/main.py` 的 `main()` 通过 `create_agent(config)` 创建 Agent
  - 证据:main.py 第 406-407 行 `from nexus.core.factory import create_agent` + `agent = create_agent(config)`。

- [x] `nexus/cli/main.py` 的 `_sessions_restore` 通过 `create_agent(config)` 创建 Agent
  - 证据:main.py 第 649-650 行 `from nexus.core.factory import create_agent` + `agent = create_agent(config)`。

- [x] `nexus/server/app.py` 的 `create_app()` 通过 `create_agent(config)` 创建 Agent
  - 证据:app.py 第 419-420 行 `from nexus.core.factory import create_agent` + `agent = create_agent(config)`(llm 未注入分支)。

- [x] `nexus/server/app.py` 不再从 `nexus.cli.main` import `_create_llm` / `_register_tools`
  - 证据:Grep `from nexus\.cli\.main import` 在 app.py 无匹配;Grep `_create_llm|_register_tools` 在 app.py 无匹配。

- [x] `PUT /api/config` 保存后调用 `create_agent(cfg)` 重建并替换 `app.state.agent`
  - 证据:app.py 第 460-464 行 `new_agent = create_agent(cfg)` → `request.app.state.agent = new_agent`。

- [ ] `nexus/cli/repl.py` 的 `/clear` 通过 `create_agent(config)` 重建 Agent
  - 状态:FAIL(部分达成)
  - 证据:repl.py 第 529-548 行 `/clear` 命令未调用 `create_agent(config)`。实际做法:从 `nexus.core.factory` 导入 `register_tools`,手动构造新 `Agent`(复用 `original_agent.llm` 与 `original_agent.policy.__class__`),再调用 `register_tools(new_agent, self.config)`。
  - 说明:已实现与 cli.main 解耦(从 core.factory 导入),但未走 `create_agent(config)` 统一入口。推测为有意设计——/clear 仅清空上下文,需保留原 LLM 实例(避免重建连接/丢失运行时状态),而 `create_agent(config)` 会重建 LLM。若严格要求统一入口,需在 factory 增加支持"复用 LLM"的变体或在 repl 改用 create_agent。

- [x] `nexus/cli/repl.py` 不再从 `nexus.cli.main` import `_register_tools`
  - 证据:Grep `from nexus\.cli\.main import` 在 repl.py 无匹配;Grep `_create_llm|_register_tools` 在 repl.py 无匹配。

- [x] `tests/test_factory.py` 覆盖三个 provider 分支
  - 证据:`TestCreateLlm` 含 `test_openai_provider`、`test_anthropic_provider`、`test_minimax_provider`(另含默认 model 回退、大小写不敏感、未知 provider 回退等用例),15 个用例全部 PASSED。

- [x] `tests/test_factory.py` 覆盖工具过滤(空列表=全部、指定列表=过滤)
  - 证据:`TestRegisterTools.test_register_all_when_enabled_empty`(enabled=[] → 5 个工具)、`test_register_subset_when_enabled_specified`(enabled=["read_file","shell"] → 2 个工具)均 PASSED。

- [ ] 全量 `pytest tests/` 通过
  - 状态:FAIL(但失败与本次重构无关)
  - 证据:`pytest tests/ -q` 结果 `2 failed, 290 passed`。失败用例为 `tests/test_cli_config.py::TestToolsConfig::test_tools_config_default_empty` 与 `test_tools_config_partial_yaml`(均断言 `config.tools.enabled == []`,实际返回全量工具列表)。
  - 关键结论:通过 `git stash` 还原 main.py/repl.py/app.py 至重构前版本后,这 2 个用例**同样失败**,证明为**预存失败**(pre-existing),与工厂重构无关(config.py 未被本次重构修改)。工厂专属测试 `tests/test_factory.py` 15/15 全部通过。

- [ ] 手动验证:PUT /api/config 切换 provider 后新对话使用新 LLM
  - 状态:SKIP(手动验证项,自动化环境无法执行)
  - 代码层支持:app.py 第 460-464 行 PUT /api/config 保存后 `create_agent(cfg)` 重建并替换 `app.state.agent`,新连接的 `_ChatConnection` 使用 `ws.app.state.agent`,逻辑上会使用新 LLM。

- [ ] 手动验证:CLI `nexus` REPL `/clear` 命令正常工作
  - 状态:SKIP(手动验证项,自动化环境无法执行)
  - 代码层支持:repl.py `/clear` 重建 Agent(复用 LLM)+ `register_tools` + 清空 `_conversation_history`,逻辑自洽。

## 汇总

| # | 检查点 | 结果 |
|---|--------|------|
| 1 | factory.py 导出 create_agent | PASS |
| 2 | create_agent 封装三步 | PASS |
| 3 | 三个 provider 分支 | PASS |
| 4 | 工具注册过滤逻辑 | PASS |
| 5 | main() 用 create_agent | PASS |
| 6 | _sessions_restore 用 create_agent | PASS |
| 7 | create_app() 用 create_agent | PASS |
| 8 | app.py 不再 import cli.main 私有函数 | PASS |
| 9 | PUT /api/config 重建 agent | PASS |
| 10 | repl /clear 用 create_agent | FAIL(用 register_tools + 手动构造,保留原 LLM) |
| 11 | repl.py 不再 import cli.main 私有函数 | PASS |
| 12 | 测试覆盖三个 provider | PASS |
| 13 | 测试覆盖工具过滤 | PASS |
| 14 | 全量 pytest 通过 | FAIL(2 个预存失败,与重构无关) |
| 15 | 手动验证 PUT /api/config | SKIP |
| 16 | 手动验证 /clear | SKIP |

通过:12 / 16
未通过:2(检查点 10、14)
跳过:2(检查点 15、16,手动验证)
