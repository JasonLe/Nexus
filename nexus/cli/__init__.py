"""Nexus CLI 工具模块 —— 提供命令行 Agent 入口。

包含:
- main.py:   CLI 主入口（argparse + 命令派发）
- repl.py:   交互式 REPL（prompt_toolkit + Rich）
- display.py: 终端输出美化（Rich 组件）
- config.py: 配置管理（参数/环境变量/配置文件）
- session.py: 会话管理（保存/加载/历史）
- tools/:     CLI 专用内置工具（文件、Shell、搜索）
"""
