"""LLM 调用重试工具 —— 指数退避 + 按错误类型决策。

设计思路
--------
provider 层（OpenAI / Anthropic）共享同一套重试逻辑：
1. 捕获异常后，判断是否为 ``LLMRetryableError`` 子类——只有可重试错误才重试。
2. 指数退避：基础间隔 × 2^(attempt) + 随机抖动，避免 thundering herd。
3. 429 限流时尊重 ``Retry-After`` 信息（若异常携带）。
4. 达到最大重试次数后抛出最后一次异常。

不可重试错误（如 ``LLMAuthError``、普通 ``LLMError``）立即抛出，不做无意义重试。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, TypeVar

from nexus.core.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMRetryableError,
    LLMServerError,
    LLMTimeoutError,
)
from nexus.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# 默认重试配置
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0  # 基础延迟（秒）
DEFAULT_MAX_DELAY = 30.0  # 单次最大延迟（秒）


async def with_retry(
    func: Callable[[], Awaitable[T]],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    operation_name: str = "LLM call",
) -> T:
    """对 async 函数执行指数退避重试。

    仅对 ``LLMRetryableError``（及其子类 RateLimit/Timeout/ServerError）重试；
    ``LLMAuthError`` 和普通 ``LLMError`` 立即抛出。

    Parameters
    ----------
    func : callable returning awaitable
        待重试的异步操作（无参，通常用 lambda 包裹）。
    max_retries : int
        最大重试次数（不含首次调用）。默认 3。
    base_delay : float
        基础退避延迟（秒）。第 n 次重试延迟 = min(max_delay, base_delay × 2^n) + jitter。
    max_delay : float
        单次延迟上限（秒）。
    operation_name : str
        操作名称，用于日志。

    Returns
    -------
    T
        func 的返回值。

    Raises
    ------
    LLMError
        重试耗尽后抛出最后一次的 LLM 异常；不可重试错误立即抛出。
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except LLMRetryableError as e:
            last_exc = e
            if attempt >= max_retries:
                logger.error(
                    "%s failed after %d retries: %s",
                    operation_name,
                    max_retries,
                    e,
                )
                raise
            # 计算退避延迟
            delay = min(max_delay, base_delay * (2 ** attempt))
            # 加入 ±25% 的抖动，避免多个客户端同步重试
            jitter = random.uniform(0.75, 1.25)
            delay *= jitter

            # 429 限流时使用更长的延迟
            if isinstance(e, LLMRateLimitError):
                delay = max(delay, base_delay * (2 ** (attempt + 1)))

            logger.warning(
                "%s attempt %d/%d failed (retryable), retrying in %.2fs: %s",
                operation_name,
                attempt + 1,
                max_retries + 1,
                delay,
                e,
            )
            await asyncio.sleep(delay)
        except (LLMAuthError, LLMError) as e:
            # 不可重试的 LLM 错误，立即抛出
            logger.error("%s failed (non-retryable): %s", operation_name, e)
            raise

    # 理论上不会到达，保险兜底
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{operation_name}: unreachable state in with_retry")
