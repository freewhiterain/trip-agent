"""外部只读工具的超时、重试、缓存、去重和熔断。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class ExternalServiceError(RuntimeError):
    pass


class CircuitOpenError(ExternalServiceError):
    pass


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


class ResilientExecutor:
    """为幂等只读调用提供单进程可靠性保护。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        max_retries: int = 2,
        failure_threshold: int = 3,
        reset_after_seconds: float = 30,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold
        self.reset_after_seconds = reset_after_seconds
        self._cache: dict[str, CacheEntry] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    def _check_circuit(self) -> None:
        if self._opened_at is None:
            return
        if time.monotonic() - self._opened_at >= self.reset_after_seconds:
            self._opened_at = None
            self._failures = 0
            return
        raise CircuitOpenError("外部服务熔断器处于开启状态")

    async def execute(
        self,
        cache_key: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: float,
    ) -> Any:
        self._check_circuit()
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > now:
            return cached.value

        async with self._lock:
            existing = self._inflight.get(cache_key)
            if existing is None:
                existing = asyncio.create_task(self._execute_with_retry(operation))
                self._inflight[cache_key] = existing

        try:
            value = await asyncio.shield(existing)
            self._cache[cache_key] = CacheEntry(value, time.monotonic() + ttl_seconds)
            return value
        finally:
            async with self._lock:
                if self._inflight.get(cache_key) is existing and existing.done():
                    self._inflight.pop(cache_key, None)

    async def _execute_with_retry(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                value = await asyncio.wait_for(operation(), timeout=self.timeout_seconds)
                self._failures = 0
                return value
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(min(0.1 * (2**attempt), 1.0))

        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()
        raise ExternalServiceError(f"外部只读调用失败：{type(last_error).__name__}") from last_error
