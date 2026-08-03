"""Nexus Server —— 面向桌面 UI 的 HTTP + WebSocket 后端适配层。

设计思路
--------
server 层只做「适配」：复用 nexus.cli 的配置加载、LLM 工厂、工具注册
和会话管理能力，将其包装为 FastAPI 应用对外提供 REST / WebSocket API。
不修改 nexus.core 下任何决策逻辑。

入口：
- ``nexus serve`` / ``nexus ui`` 命令（见 nexus.cli.main）
- ``create_app()`` 应用工厂（见 nexus.server.app）
"""

from nexus.server.app import create_app

__all__ = ["create_app"]
