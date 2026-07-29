"""Nexus 结构化日志模块。

设计思路
--------
- 提供 `get_logger(name)` 工厂函数，返回配置好的 `logging.Logger`。
- 使用 Python 标准库 `logging`，不引入第三方日志依赖。
- 默认使用 `NullHandler` —— 库代码不强制输出日志，由应用层决定 handler 策略。
- 通过自定义 `Formatter` 将 `LogRecord` 的 extra 字段展平输出，方便在结构化日志
  系统中追踪 run_id、step、tool_name 等运行时上下文。

使用方式
--------
>>> from nexus.logging import get_logger
>>> logger = get_logger(__name__)
>>> logger.info("Agent started", extra={"run_id": "abc-123", "step": 1})
"""

from __future__ import annotations

import logging
from typing import Any


_EXTRA_KEY_WHITELIST: set[str] = {
    "run_id",
    "step",
    "tool_name",
    "tool_call_id",
    "agent_name",
    "session_id",
    "user_id",
    "model",
    "provider",
}


class NexusFormatter(logging.Formatter):
    """将 LogRecord 的 extra 字段展平输出的结构化 Formatter。

    格式：``<asctime> | <name> | <levelname> | <message> | <extra_fields>``

    extra_fields 从 LogRecord 的 ``__dict__`` 中提取白名单字段，
    以 ``key=value`` 的 k=v 对形式展示。缺失的字段不会出现在输出中。
    """

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        base: str = super().format(record)

        extras: list[str] = []
        for key in sorted(_EXTRA_KEY_WHITELIST):
            value = record.__dict__.get(key)
            if value is not None:
                extras.append(f"{key}={value}")

        if extras:
            return f"{base} | {' '.join(extras)}"
        return base


def get_logger(name: str) -> logging.Logger:
    """获取结构化日志 Logger。

    返回的 Logger 已配置好 ``NexusFormatter`` 和一个 ``NullHandler``。
    应用层可以通过 ``logger.handlers.clear()`` + ``addHandler(...)`` 覆盖日志策略。

    Parameters
    ----------
    name : str
        Logger 名称，通常传入 ``__name__``。

    Returns
    -------
    logging.Logger
        配置好的 Logger 实例。
    """
    logger = logging.getLogger(name)

    # 避免重复添加 handler（模块热加载等场景）
    if not any(isinstance(h, logging.NullHandler) for h in logger.handlers):
        handler = logging.NullHandler()
        handler.setFormatter(NexusFormatter())
        logger.addHandler(handler)

    return logger
