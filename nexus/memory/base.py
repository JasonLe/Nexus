"""Memory 抽象层 - 定义记忆项的通用数据结构和记忆系统的统一接口。

设计目标
--------
将 Memory 从 Agent Core 中解耦，Agent 通过 BaseMemory 接口存取记忆，
不感知底层是内存、向量数据库还是图数据库。

为何不直接绑定 Vector DB？
~~~~~~~~~~~~~~~~~~~~~~~~~~
- **场景多样性**：短期对话缓存（FIFO 即可）、长期知识检索（需要向量语义搜索）、
  结构化关系记忆（需要图数据库）各需不同的存储后端。
- **存储可替换**：允许在 Chroma、Pinecone、Weaviate、Neo4j 等之间自由切换，
  不锁定供应商。
- **渐进复杂度**：用户可能只需要短期记忆，不应强制引入向量数据库依赖。
- **统一接口**：所有 Memory 类型操作相同的 MemoryItem 结构，通过 metadata
  区分类型和属性，上层代码无需关注底层实现差异。

save / search / delete / forget 的语义约定
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- ``save``：将 MemoryItem 持久化存储，返回唯一标识符。实现应处理持久化和
  连接管理，调用方无需关心存储细节。
- ``search``：语义相似度检索。返回的 MemoryItem 按相似度降序排列，
  ``score`` 字段含义由实现定义（余弦相似度、距离等），调用方根据分数阈值自行过滤。
- ``delete``：按 ID 精确删除单条记忆，返回是否成功。
- ``forget``：语义模糊删除。先执行 search 找出相关记忆，再批量删除。
  适用场景：用户请求"忘记关于 X 的信息"。返回实际删除的数量。

实现时的注意事项
~~~~~~~~~~~~~~~~
- 所有方法都是 async，因为实际存储后端几乎总是涉及 I/O。
- 子类应在 ``__init__`` 中完成连接/索引初始化，在 ``close()`` 中释放资源。
- 如果存储后端不支持语义搜索，`search` 可通过 keyword 匹配降级实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from nexus.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MemoryItem:
    """记忆项 - Memory 系统中存储的基本单元。

    统一的记忆项数据结构，屏蔽底层存储差异。Short-term / Long-term /
    Vector / Graph 等不同 Memory 实现都操作 MemoryItem，
    通过 ``metadata`` 区分类型和其他属性。

    Attributes
    ----------
    id : str
        唯一标识符，默认自动生成 UUID。
    content : str
        记忆的文本内容，用于语义搜索的原始文本。
    metadata : dict[str, Any]
        扩展元数据。约定用法：

        - ``"type"``：记忆类型标签（如 ``"chat"``, ``"fact"``, ``"tool_result"``）
        - ``"source"``：记忆来源标识（如 ``"user_input"``, ``"llm_response"``, ``"tool_call"``）
        - ``"session_id"``：关联的会话 ID
        - ``"run_id"``：关联的运行 ID
        - ``"importance"``：重要性评分（用于记忆提炼/衰减策略）
        - ``"tags"``：用户自定义标签列表

        实现可约定更多字段，但应在其文档中明确说明。
    created_at : datetime
        创建时间戳（UTC），默认记录创建时刻。
    score : float
        相似度分数，由 ``search`` 方法返回时填充。分数含义由具体实现定义，
        ————般而言越高越相关。创建/保存时不设置此字段。
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    score: float = 0.0


class BaseMemory(ABC):
    """Memory 抽象基类 - 不绑定任何具体存储后端。

    设计思路：将 Memory 从 Agent Core 中解耦。Agent 通过此接口
    存取记忆，不感知底层是内存、向量数据库还是图数据库。

    扩展约定
    --------
    - 每个子类代表一种 Memory 类型（ShortTerm / Vector / Graph 等）。
    - ``save`` / ``search`` 是核心方法，``delete`` / ``forget`` 用于记忆管理。
    - ``search`` 返回带相似度分数的列表，分数含义由实现定义。
    - 实现应处理持久化和连接管理。

    调用时机（预留，当前 MVP 不主动调用）
    ------------------------------------
    - ``Agent.run`` 前后 Runtime 可调用 ``save`` 写入上下文。
    - Policy 可调用 ``search`` 检索相关记忆供 LLM 使用。
    - 记忆清理任务可调用 ``delete`` / ``forget`` 管理记忆生命周期。
    """

    @abstractmethod
    async def save(self, item: MemoryItem) -> str:
        """保存记忆项，返回记忆 ID。

        Parameters
        ----------
        item : MemoryItem
            待保存的记忆项。如果 ``item.id`` 已存在，实现应决定是覆盖还是报错。

        Returns
        -------
        str
            已保存记忆的唯一标识符。
        """
        ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        """语义搜索相关记忆，按相关度降序排列。

        Parameters
        ----------
        query : str
            搜索查询文本。
        top_k : int
            返回的最相关记忆数量上限，默认 5。

        Returns
        -------
        list[MemoryItem]
            匹配的记忆项列表，每个 ``MemoryItem.score`` 已填充相似度分数，
            按分数降序排列。无结果时返回空列表。
        """
        ...

    @abstractmethod
    async def delete(self, item_id: str) -> bool:
        """删除指定记忆。

        Parameters
        ----------
        item_id : str
            要删除的记忆的唯一标识符。

        Returns
        -------
        bool
            True 表示已删除，False 表示 ID 不存在或删除失败。
        """
        ...

    @abstractmethod
    async def forget(self, query: str, top_k: int = 5) -> int:
        """忘记与查询相关的记忆（语义搜索 + 批量删除）。

        先执行 ``search(query, top_k)`` 找到相关记忆，再逐一删除。
        适用场景：用户请求"忘记之前关于 X 的讨论"。

        Parameters
        ----------
        query : str
            要忘记的记忆主题/查询文本。
        top_k : int
            最多删除的记忆数量上限，默认 5。

        Returns
        -------
        int
            实际删除的记忆数量。
        """
        ...
