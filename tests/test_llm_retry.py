"""LLM 重试逻辑测试 —— with_retry 行为验证。"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from nexus.core.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMRetryableError,
    LLMServerError,
    LLMTimeoutError,
)
from nexus.llm._retry import with_retry


class TestWithRetry:
    """with_retry 重试行为测试。"""

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """首次成功不重试。"""
        func = AsyncMock(return_value="ok")
        result = await with_retry(func, operation_name="test")
        assert result == "ok"
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_retryable_error_retried(self):
        """可重试错误触发重试。"""
        func = AsyncMock(
            side_effect=[LLMServerError("fail"), "ok"]
        )
        with patch("nexus.llm._retry.asyncio.sleep", new_callable=AsyncMock):
            result = await with_retry(func, max_retries=2, operation_name="test")
        assert result == "ok"
        assert func.call_count == 2

    @pytest.mark.asyncio
    async def test_auth_error_not_retried(self):
        """鉴权错误不重试，立即抛出。"""
        func = AsyncMock(side_effect=LLMAuthError("bad key"))
        with pytest.raises(LLMAuthError):
            await with_retry(func, max_retries=3, operation_name="test")
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_plain_llm_error_not_retried(self):
        """普通 LLMError（非 Retryable）不重试。"""
        func = AsyncMock(side_effect=LLMError("unknown"))
        with pytest.raises(LLMError):
            await with_retry(func, max_retries=3, operation_name="test")
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """重试耗尽后抛出最后的可重试错误。"""
        func = AsyncMock(side_effect=LLMRateLimitError("limited"))
        with patch("nexus.llm._retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(LLMRateLimitError):
                await with_retry(func, max_retries=2, operation_name="test")
        # 首次 + 2 次重试 = 3
        assert func.call_count == 3

    @pytest.mark.asyncio
    async def test_timeout_error_retried(self):
        """超时错误是可重试的。"""
        func = AsyncMock(
            side_effect=[LLMTimeoutError("timeout"), "ok"]
        )
        with patch("nexus.llm._retry.asyncio.sleep", new_callable=AsyncMock):
            result = await with_retry(func, max_retries=2, operation_name="test")
        assert result == "ok"
        assert func.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_zero(self):
        """max_retries=0 表示不重试。"""
        func = AsyncMock(side_effect=LLMServerError("fail"))
        with pytest.raises(LLMServerError):
            await with_retry(func, max_retries=0, operation_name="test")
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_sleep_called_between_retries(self):
        """重试之间调用 sleep。"""
        sleep_mock = AsyncMock()
        func = AsyncMock(side_effect=[LLMServerError("fail"), "ok"])
        with patch("nexus.llm._retry.asyncio.sleep", sleep_mock):
            await with_retry(func, max_retries=2, operation_name="test")
        assert sleep_mock.call_count == 1


class TestErrorHierarchy:
    """错误类型继承关系测试。"""

    def test_rate_limit_is_retryable(self):
        assert issubclass(LLMRateLimitError, LLMRetryableError)

    def test_server_error_is_retryable(self):
        assert issubclass(LLMServerError, LLMRetryableError)

    def test_timeout_is_retryable(self):
        assert issubclass(LLMTimeoutError, LLMRetryableError)

    def test_auth_not_retryable(self):
        assert not issubclass(LLMAuthError, LLMRetryableError)

    def test_all_are_llm_errors(self):
        for cls in [LLMRateLimitError, LLMServerError, LLMTimeoutError, LLMAuthError]:
            assert issubclass(cls, LLMError)
