# Plan: 日志分会话存储 + nexus sessions 子命令

## Summary

两项紧密关联的 CLI 基础设施增强：

1. **日志分会话存储** — 将当前单一文件 `~/.nexus/logs/nexus.log` 改为按「会话」平铺命名存储：`~/.nexus/logs/<session_id>.log`。会话删除时同步删除对应日志文件。
2. **`nexus sessions` 子命令** — 提供独立子命令管理历史会话：`list` / `delete <id>` / `restore <id>`，删除与恢复均带二次确认（可用 `-y` 跳过）。

两者通过 `session_id` 串联：每个 CLI 进程启动时生成一个 `run_id`，既用作日志文件名，也作为 `SessionManager.save()` 的 session_id，从而保证「会话 JSON 文件名」与「日志文件名」一一对应，删除时同步清理。

---

## Current State Analysis

### 日志现状
- `nexus/logging.py`：`NexusFormatter` 将 LogRecord 的 extra 字段（`run_id` / `session_id` / `step` / `tool_name` 等）展平输出
- `nexus/cli/main.py:setup_logging()`（`main.py:38-83`）：
  - `StreamHandler` 输出到终端，级别由 verbose/debug 控制
  - `RotatingFileHandler` 写入 `~/.nexus/logs/nexus.log`（10MB × 5 备份，DEBUG 级别）
  - **所有进程、所有会话共用同一个日志文件**，无法按会话隔离
- 当前日志中虽带 `session_id` extra 字段，但实际无人设置（搜索代码未见 `extra={"session_id": ...}` 的实际调用点）

### 会话管理现状
- `nexus/cli/session.py:SessionManager`：每个会话保存为 `~/.nexus/sessions/<session_id>.json`
- `save()`（`session.py:82-140`）：**每次调用都生成新的 8 位 uuid 前缀**作为 session_id。同一 REPL 进程多次 `/save` 会产生多个 JSON 文件而非覆盖（docs 已标注为「潜在改进点 #5」）
- `delete()`（`session.py:247-274`）：只 `unlink()` JSON 文件，**不清理任何关联日志**
- `load_latest()` / `list_sessions()` / `load()` 均正常工作

### CLI 命令现状
- `nexus/cli/main.py:main()`（`main.py:308-413`）使用单一 `argparse.ArgumentParser` + 位置参数 `prompt`（`nargs="?"`）
- 现有相关标志：`--continue`（恢复最近会话）、`--list-sessions`（简单文本列出，每行一条）
- `--list-sessions` 仅 `print(f"  {id}  {timestamp}  {summary}")`，无表格美化、无删除、无按 ID 恢复能力
- 不存在 `nexus sessions` 子命令入口

### 关键文件
| 文件 | 当前角色 | 需改动程度 |
|------|---------|-----------|
| [nexus/logging.py](file:///d:/Nexus/nexus/logging.py) | NexusFormatter + NullHandler | 不变 |
| [nexus/cli/main.py](file:///d:/Nexus/nexus/cli/main.py) | CLI 入口 + setup_logging + 命令派发 | 主要改动 |
| [nexus/cli/session.py](file:///d:/Nexus/nexus/cli/session.py) | SessionManager CRUD | 中等改动 |
| [nexus/cli/repl.py](file:///d:/Nexus/nexus/cli/repl.py) | REPL + 内置命令 + 会话保存 | 小改动 |
| [nexus/cli/display.py](file:///d:/Nexus/nexus/cli/display.py) | Rich 终端渲染 | 新增表格渲染方法 |
| [tests/test_cli_session.py](file:///d:/Nexus/tests/test_cli_session.py) | SessionManager 测试 | 新增测试用例 |

### 现有约定参考
- [docs/agent-loop-and-session.md](file:///d:/Nexus/docs/agent-loop-and-session.md) §7.2 已标注「save 每次生成新 session_id」为待改进项
- [add-minimax-config-logpersist.md](file:///d:/Nexus/.trae/documents/add-minimax-config-logpersist.md) 中关于日志持久化的既有约定：文件日志始终 DEBUG 级别、终端级别由 verbose/debug 控制
- [display.py:400-429](file:///d:/Nexus/nexus/cli/display.py#L400-429) 已有 `render_summary()` 使用 `rich.table.Table` 的模式可借鉴

---

## Proposed Changes

### 1. 日志分会话存储

**What**: 将日志文件路径从 `~/.nexus/logs/nexus.log` 改为 `~/.nexus/logs/<session_id>.log`（平铺、不分日），并使会话删除时同步清理对应日志。

**Why**:
- 分会话文件让每个对话的日志完全隔离，删除会话即清理该会话全部日志痕迹
- 当前 `RotatingFileHandler` 在多会话混写时无法按会话切分，删除会话时也无法只删该会话的日志
- 平铺命名（不分日目录）简化路径计算与删除逻辑：`~/.nexus/logs/<session_id>.log` 一对一映射 `~/.nexus/sessions/<session_id>.json`，路径推导直观

**How**:

#### a. `nexus/cli/main.py` — `setup_logging()` 改造

签名变更：

```python
def setup_logging(
    verbose: bool = False,
    debug: bool = False,
    session_id: str | None = None,   # 新增
    log_dir: str | None = None,     # 新增（覆盖默认 ~/.nexus/logs/）
) -> str:
    """返回当前会话日志文件的绝对路径。"""
```

行为变更：
- 终端 `StreamHandler` 保持不变
- 文件日志路径计算：
  - `log_root = Path(log_dir or (Path.home() / ".nexus" / "logs"))`
  - `log_root.mkdir(parents=True, exist_ok=True)`
  - `session_id = session_id or str(uuid.uuid4())[:8]`（兜底，确保单 task 模式也有日志）
  - `log_file = log_root / f"{session_id}.log"`
- 用普通 `logging.FileHandler`（非 Rotating）写入该路径 —— 单会话文件不会过大（一次会话通常 < 1MB），且会话删除时整文件清理
- 文件日志级别仍为 DEBUG
- 返回 `str(log_file)`，供调用方在保存会话时写入 metadata

#### b. `nexus/cli/main.py:main()` — 启动时生成 `run_id`

在 `main()` 最早期（位于 `args = parser.parse_args()` 之后、`setup_logging()` 之前）：

```python
import uuid
run_id = str(uuid.uuid4())[:8]
log_file = setup_logging(
    verbose=args.verbose,
    debug=args.debug,
    session_id=run_id,
)
```

`run_id` 在后续流程中传递给：
- `Repl(..., run_id=run_id, log_file=log_file)` —— 用于 `/save` 与退出时保存
- `_run_continue(agent, config, run_id=run_id, log_file=log_file)` —— 恢复时同样用新 run_id 写新日志
- `_run_single(...)` —— 单 task 模式仅生成日志，不保存会话 JSON（保持现有行为）

#### c. `nexus/cli/session.py:SessionManager` — 接受 `session_id` 参数 + 同步删除日志

**`save()` 签名扩展**：

```python
def save(
    self,
    state: AgentState,
    metadata: dict[str, Any] | None = None,
    auto_truncate: bool = True,
    session_id: str | None = None,   # 新增；None 时保持原行为（生成新 uuid）
    log_file: str | None = None,    # 新增；非空时写入 metadata.log_file
) -> str:
```

行为：
- 若 `session_id` 传入，使用该 id；文件已存在则覆盖（解决 docs #5）
- 若 `log_file` 传入，写入 `metadata["log_file"] = log_file`
- 默认行为（`session_id=None`）保持不变，向后兼容现有测试

**`delete()` 扩展**：

```python
def delete(self, session_id: str, delete_logs: bool = True) -> bool:
    """删除会话 JSON 文件；delete_logs=True 时同步删除对应日志文件。"""
```

日志清理策略（两层兜底）：
1. 优先读取会话 JSON 的 `metadata.log_file` 字段，若存在则 `Path(log_file).unlink(missing_ok=True)`
2. 兜底：`Path.home() / ".nexus" / "logs" / f"{session_id}.log"` 直接删除（平铺路径无需 glob，metadata 缺失也能定位）
3. 若 `delete_logs=False`（保留日志用于调试），跳过上述步骤

**`list_sessions()` 微调**：
- 返回字段新增 `log_file`（从 metadata 读取，可能为 None）
- 用于 `nexus sessions` 子命令在删除时显示关联日志路径

#### d. `nexus/cli/repl.py` — 接收 `run_id` 并传递给 `save()`

`Repl.__init__()` 新增参数：

```python
def __init__(
    self,
    agent: Agent,
    display: DisplayManager,
    config: NexusConfig,
    session_mgr: SessionManager | None = None,
    run_id: str | None = None,        # 新增
    log_file: str | None = None,      # 新增
) -> None:
    ...
    self._run_id = run_id
    self._log_file = log_file
```

`_handle_command()` 中 `/save` 分支与 `_handle_exit()` 中保存逻辑：

```python
session_id = self.session_mgr.save(
    state,
    metadata={"command": cmd, "mode": "repl"},
    session_id=self._run_id,   # 同一 REPL 多次 /save 覆盖同一 JSON
    log_file=self._log_file,
)
```

#### e. `_run_continue()` 同样传入 `run_id`

恢复时启动新进程，新 run_id 用于新日志文件，旧 session JSON 不动。这样：
- `nexus --continue` 加载旧 session → 启动新 REPL → 退出时保存为新 session_id（基于新 run_id）
- 旧 session JSON + 旧日志文件保持原状（不丢失历史）

#### f. 单 task 模式 `_run_single()`

仅生成 run_id 和日志文件，不调用 `session_mgr.save()`（与当前行为一致）。日志文件作为孤儿保留在 `~/.nexus/logs/<run_id>.log`，便于排查问题；用户可手动清理或后续添加清理脚本。

---

### 2. `nexus sessions` 子命令

**What**: 在不破坏现有 `nexus "prompt"` / `nexus` / `nexus --continue` / `nexus --list-sessions` 接口的前提下，新增 `nexus sessions` 子命令树。

**Why**:
- 现有 `--list-sessions` 仅能列表，无法删除或按 ID 恢复
- 删除和恢复是危险操作，需独立子命令以承载确认交互
- `git`/`kubectl`/`docker` 风格的子命令更符合 CLI 工具惯例，便于未来扩展（如 `nexus sessions show <id>`、`nexus sessions rename`）

**How**:

#### a. `nexus/cli/main.py:main()` — 早期派发

由于现有 argparse 使用了 `prompt` 位置参数（`nargs="?"`），与 `add_subparsers()` 不兼容（`nexus sessions list` 会被解析为 `prompt="sessions"`）。采用**前置手动派发**：

```python
def main() -> None:
    # 早期派发：nexus sessions <sub> [args]
    if len(sys.argv) > 1 and sys.argv[1] == "sessions":
        return _run_sessions_command(sys.argv[2:])

    # 其余逻辑保持不变
    parser = argparse.ArgumentParser(prog="nexus", ...)
    ...
```

#### b. 新增 `_run_sessions_command(argv: list[str])` 函数

独立 argparse，不复用主 parser：

```python
def _run_sessions_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="nexus sessions",
        description="管理历史会话：列出、删除、恢复",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<command>")

    # list（默认行为）
    sub.add_parser("list", help="列出历史会话")

    # delete
    p_del = sub.add_parser("delete", help="删除指定会话")
    p_del.add_argument("session_id", help="要删除的会话 ID")
    p_del.add_argument("-y", "--yes", action="store_true", help="跳过确认直接删除")
    p_del.add_argument("--keep-logs", action="store_true", help="保留日志文件，仅删除会话 JSON")

    # restore
    p_res = sub.add_parser("restore", help="恢复指定会话")
    p_res.add_argument("session_id", help="要恢复的会话 ID")
    p_res.add_argument("-y", "--yes", action="store_true", help="跳过确认直接恢复")

    args = parser.parse_args(argv)
    subcmd = args.subcommand or "list"   # 无子命令时默认 list

    setup_logging()  # sessions 命令本身也写日志（用临时 run_id）

    from nexus.cli.session import SessionManager
    from nexus.cli.display import DisplayManager
    mgr = SessionManager()
    display = DisplayManager()

    if subcmd == "list":
        _sessions_list(mgr, display)
    elif subcmd == "delete":
        _sessions_delete(mgr, args.session_id, confirm=not args.yes,
                         keep_logs=args.keep_logs, display=display)
    elif subcmd == "restore":
        _sessions_restore(mgr, args.session_id, confirm=not args.yes, display=display)
```

#### c. `_sessions_list()` — Rich 表格展示

```python
def _sessions_list(mgr: SessionManager, display: DisplayManager) -> None:
    sessions = mgr.list_sessions()
    if not sessions:
        print("No saved sessions.")
        return
    display.render_sessions_table(sessions)
    print(f"\n共 {len(sessions)} 条会话。使用 `nexus sessions restore <id>` 恢复，`nexus sessions delete <id>` 删除。")
```

#### d. `nexus/cli/display.py` — 新增 `render_sessions_table()`

```python
def render_sessions_table(self, sessions: list[dict]) -> None:
    """渲染历史会话列表为 Rich Table。"""
    table = Table(title="Nexus Sessions", show_lines=False, border_style="cyan")
    table.add_column("ID", style="bold cyan", width=10)
    table.add_column("Created", style="dim", width=20)
    table.add_column("Msgs", justify="right", width=6)
    table.add_column("Summary", overflow="fold")
    for s in sessions:
        table.add_row(
            s["id"],
            s.get("timestamp", ""),
            str(s.get("message_count", 0)),
            s.get("summary", "(empty)"),
        )
    self.console.print(table)
```

#### e. `_sessions_delete()` — 带确认的删除

```python
def _sessions_delete(
    mgr: SessionManager,
    session_id: str,
    confirm: bool,
    keep_logs: bool,
    display: DisplayManager,
) -> None:
    # 1. 预览：先加载 session 显示给用户看
    state = mgr.load(session_id)
    if state is None:
        print(f"Session not found: {session_id}")
        return
    sessions = mgr.list_sessions()
    info = next((s for s in sessions if s["id"] == session_id), None)
    summary = info["summary"] if info else "(unknown)"
    msg_count = info["message_count"] if info else len(state.messages)
    log_file = info.get("log_file") if info else None

    print(f"\n  ID:       {session_id}")
    print(f"  Summary:  {summary}")
    print(f"  Messages: {msg_count}")
    if log_file:
        print(f"  Log file: {log_file}")
    print()

    # 2. 确认
    if confirm:
        answer = input(f"Delete session {session_id}? This cannot be undone. (y/N) ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    # 3. 执行删除
    ok = mgr.delete(session_id, delete_logs=not keep_logs)
    if ok:
        print(f"Session {session_id} deleted.")
        if keep_logs:
            print("(Logs retained per --keep-logs)")
    else:
        print(f"Failed to delete session {session_id}.")
```

#### f. `_sessions_restore()` — 带确认的恢复

```python
def _sessions_restore(
    mgr: SessionManager,
    session_id: str,
    confirm: bool,
    display: DisplayManager,
) -> None:
    state = mgr.load(session_id)
    if state is None:
        print(f"Session not found: {session_id}")
        return

    sessions = mgr.list_sessions()
    info = next((s for s in sessions if s["id"] == session_id), None)
    summary = info["summary"] if info else ""
    msg_count = info["message_count"] if info else len(state.messages)

    print(f"\n  ID:       {session_id}")
    print(f"  Summary:  {summary}")
    print(f"  Messages: {msg_count}")
    print()

    if confirm:
        answer = input(f"Restore session {session_id} and start REPL? (y/N) ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return

    # 3. 启动 REPL 并恢复
    # 复用 _run_continue 的模式，但用指定 session 而非 latest
    from nexus.cli.config import load_config
    from nexus.cli.repl import Repl
    from nexus.cli.main import _create_llm, _register_tools
    import asyncio, uuid

    run_id = str(uuid.uuid4())[:8]
    log_file = setup_logging(session_id=run_id)

    config = load_config(work_dir=os.getcwd())
    config.work_dir = os.getcwd()
    llm = _create_llm(config)
    agent = Agent(llm=llm, system_prompt=config.system_prompt, max_steps=config.max_steps)
    _register_tools(agent, config)

    display.show_welcome()
    repl = Repl(agent=agent, display=display, config=config, run_id=run_id, log_file=log_file)
    asyncio.run(repl.restore_session(state))
    asyncio.run(repl.run())
```

为避免循环 import，`_sessions_restore` 在函数内部局部 import。

#### g. 向后兼容 `--list-sessions` 与 `--continue`

- 保留 `--list-sessions` 标志，行为改为「调用 `_sessions_list()`」（共享渲染逻辑，向后兼容但视觉升级）
- 保留 `--continue` 标志，行为不变（恢复最近会话）
- 这两个标志**不再推荐使用**，但代码中不打印 deprecation warning（避免噪音）；后续可在文档/帮助中标注「推荐使用 `nexus sessions list` / `nexus sessions restore latest`」

#### h. 帮助文本更新

`nexus --help` 输出末尾添加提示：
```
Subcommands:
  sessions           管理历史会话（list/delete/restore）

Run `nexus sessions --help` for subcommand details.
```

---

## Files Changed

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| [nexus/cli/main.py](file:///d:/Nexus/nexus/cli/main.py) | 修改 | `setup_logging()` 加 `session_id`/`log_dir` 参数、返回路径；`main()` 生成 `run_id` 并早期派发 `sessions` 子命令；新增 `_run_sessions_command` / `_sessions_list` / `_sessions_delete` / `_sessions_restore`；`--list-sessions` 复用新渲染；`_run_continue` 与 `_run_single` 接受 `run_id`/`log_file` |
| [nexus/cli/session.py](file:///d:/Nexus/nexus/cli/session.py) | 修改 | `save()` 加 `session_id`/`log_file` 参数；`delete()` 加 `delete_logs` 参数并实现日志同步删除；`list_sessions()` 返回字段加 `log_file` |
| [nexus/cli/repl.py](file:///d:/Nexus/nexus/cli/repl.py) | 修改 | `Repl.__init__()` 加 `run_id`/`log_file` 参数；`/save` 与 `_handle_exit` 调用 `session_mgr.save()` 时传入 |
| [nexus/cli/display.py](file:///d:/Nexus/nexus/cli/display.py) | 修改 | 新增 `render_sessions_table(sessions)` 方法 |
| [tests/test_cli_session.py](file:///d:/Nexus/tests/test_cli_session.py) | 修改 | 新增测试：`save(session_id=...)` 覆盖行为、`delete(delete_logs=True)` 同步删除日志、`list_sessions()` 含 `log_file` 字段 |

**不新增文件**，全部改动落在现有模块内（遵循 CLAUDE 风格的「prefer editing existing files」原则）。

---

## Assumptions & Decisions

1. **日志平铺、不分日目录**：`~/.nexus/logs/<session_id>.log` 与 `~/.nexus/sessions/<session_id>.json` 路径一一对应，删除逻辑直观（直接拼路径即可，无需 glob 跨日期目录）。
2. **日志文件不再使用 RotatingFileHandler**：单会话日志通常 < 1MB，无需轮转；多会话隔离已通过分文件解决。如果未来单会话日志超大，可再加 `RotatingFileHandler`（backupCount 不影响文件名匹配）。
3. **单 task 模式（`nexus "prompt"`）的孤儿日志**：保留在 `~/.nexus/logs/<run_id>.log` 不自动清理。理由：单 task 模式无 session JSON，无法关联删除；保留日志便于排查问题；如用户需清理可手动删除或后续添加 `nexus logs clean` 子命令。
4. **`save(session_id=...)` 行为变更**：传入 `session_id` 时，文件已存在则覆盖。**这不破坏现有测试**——`save()` 默认 `session_id=None` 仍生成新 uuid，`test_list_sessions` 调用两次 save 得到两个不同 id 仍然成立。仅 REPL 流程使用新参数实现覆盖式保存。
5. **删除日志的两层兜底**：优先读 metadata.log_file，再直接拼路径 `~/.nexus/logs/<session_id>.log` 兜底。平铺路径下兜底无需 glob，O(1) 查找。
6. **`--keep-logs` 选项**：删除会话时若想保留日志用于事后排查，使用 `nexus sessions delete <id> --keep-logs`。
7. **`-y/--yes` 跳过确认**：脚本化场景下用户可加 `-y` 跳过交互提示。
8. **不使用 argparse subparsers 与现有位置参数共存**：因 `prompt` 是 `nargs="?"` 位置参数，与 subparsers 冲突。采用 `sys.argv[1] == "sessions"` 早期手动派发，最简洁且不破坏现有接口。
9. **`--continue` 恢复时使用新 run_id**：旧 session JSON 和旧日志文件不动，新进程写新日志，退出时保存为新 session_id。这保留了历史快照（用户可恢复多个时间点的会话状态）。
10. **`list_sessions()` 仍最多返回 20 条**（`_DEFAULT_LIST_LIMIT`）：当前已有限制不变；未来如有更多会话需求可加 `--limit` 参数。
11. **日志路径返回值**：`setup_logging()` 返回 `str(log_file)`，便于 main() 写入 session metadata。返回值类型从 `None` 变为 `str`，是 API 变更但向后兼容（旧调用方忽略返回值即可）。
12. **`session_id` 长度保持 8 位**：与现有 `_SESSION_ID_LENGTH` 一致，作为日志文件名足够唯一，且与现有 JSON 文件名命名规则统一。

---

## Verification

### 单元测试（新增 / 更新 `tests/test_cli_session.py`）

```bash
python -m pytest tests/test_cli_session.py -v
```

新增测试用例：
1. `test_save_with_explicit_session_id_overwrites` — 同一 session_id 两次 save 后只存在一个 JSON 文件
2. `test_save_with_log_file_metadata` — 传入 `log_file` 参数后，JSON 文件 `metadata.log_file` 字段正确
3. `test_delete_removes_log_file` — 在 `~/.nexus/logs/<id>.log` 创建日志文件后调用 `delete(id)`，日志文件也被删除
4. `test_delete_keep_logs` — `delete(id, delete_logs=False)` 只删 JSON 不删日志
5. `test_delete_with_path_fallback` — metadata 缺失 log_file 时，拼路径兜底仍能删除日志
6. `test_list_sessions_includes_log_file` — `list_sessions()` 返回项含 `log_file` 字段

### CLI 集成测试（手动）

```bash
# 1. 启动 REPL 并保存多个会话
nexus
> /save
> hello
> /save   # 覆盖同一 JSON（同一 run_id）
> /quit

# 2. 列出会话
nexus sessions list
nexus sessions                 # 默认 list
nexus --list-sessions          # 向后兼容

# 3. 删除会话（带确认）
nexus sessions delete abc12345
# > 预览 → (y/N) → 删除 → 验证 JSON 和日志文件均不存在
nexus sessions delete abc12345 -y            # 跳过确认
nexus sessions delete abc12345 --keep-logs   # 仅删 JSON

# 4. 恢复会话（带确认）
nexus sessions restore abc12345
# > 预览 → (y/N) → 启动 REPL → 验证上下文恢复

# 5. 恢复最近会话（向后兼容）
nexus --continue

# 6. 验证日志分会话命名
ls ~/.nexus/logs/
# 应看到 <session_id>.log 文件（与 sessions 目录下 JSON 同名）

# 7. 删除会话后验证日志清理
ls ~/.nexus/logs/<deleted_id>.log
# 应不存在
```

### 全量回归

```bash
python -m pytest tests/ -v
```

确保现有 114 个测试不受影响（特别是 `test_cli_session.py` 与 `test_cli_config.py`）。

---

## Implementation Order

1. **`session.py`** — `save()` / `delete()` / `list_sessions()` 签名扩展与日志同步删除逻辑（最先改动，独立可测）
2. **`tests/test_cli_session.py`** — 新增测试用例验证 step 1
3. **`main.py`** — `setup_logging()` 改造 + `main()` 生成 `run_id` + `_run_continue`/`_run_single` 接受参数
4. **`repl.py`** — 接受 `run_id`/`log_file` 参数并传入 `save()`
5. **`display.py`** — 新增 `render_sessions_table()`
6. **`main.py`** — 新增 `_run_sessions_command` / `_sessions_list` / `_sessions_delete` / `_sessions_restore` + 早期派发
7. **`main.py`** — `--list-sessions` 改为复用 `_sessions_list`，帮助文本更新
8. **手动集成验证** — 按 Verification 章节的 CLI 集成测试逐项验证
