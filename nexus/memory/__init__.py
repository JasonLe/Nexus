"""Memory 模块 - 记忆系统的抽象和实现。

提供统一的记忆项数据结构（MemoryItem）和记忆系统接口（BaseMemory），
将 Memory 从 Agent Core 中解耦。
"""

from nexus.memory.base import BaseMemory, MemoryItem

__all__ = ["BaseMemory", "MemoryItem"]
