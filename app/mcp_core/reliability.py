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


# 缓存条目上限。search 的 cache_key 里带完整 query 文本
# （tavily:{query}:{max_results}），用户输入的多样性会直接变成常驻内存，
# 因此必须有上界而不只是靠 TTL 过期。
_MAX_CACHE_ENTRIES = 512


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

    def _cached_value(self, cache_key: str, now: float) -> tuple[bool, Any]:
        entry = self._cache.get(cache_key)
        if entry is None:
            return False, None
        if entry.expires_at <= now:
            # 顺手清掉自己这条过期项，避免只读路径上留垃圾。
            self._cache.pop(cache_key, None)
            return False, None
        return True, entry.value

    def _store(self, cache_key: str, value: Any, ttl_seconds: float) -> None:
        now = time.monotonic()
        self._cache[cache_key] = CacheEntry(value, now + ttl_seconds)
        if len(self._cache) <= _MAX_CACHE_ENTRIES:
            return
        # 先回收所有已过期的条目；只有仍然放不下时才按最早到期的顺序淘汰。
        for key in [key for key, entry in self._cache.items() if entry.expires_at <= now]:
            self._cache.pop(key, None)
        if len(self._cache) <= _MAX_CACHE_ENTRIES:
            return
        for key, _entry in sorted(self._cache.items(), key=lambda item: item[1].expires_at)[
            : len(self._cache) - _MAX_CACHE_ENTRIES
        ]:
            self._cache.pop(key, None)

    async def execute(
        self,
        cache_key: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: float,
    ) -> Any:
        # 先查缓存再查熔断：缓存的意义恰恰是上游挂掉时还能答上来。原先顺序相反，
        # 熔断期间连手上已有、还没过期的答案都返回不了；而熔断器是全局的，
        # 天气连挂三次会让另一个 key 上完全健康的搜索缓存一起被拒。
        hit, value = self._cached_value(cache_key, time.monotonic())
        if hit:
            return value
        self._check_circuit()

        async with self._lock:
            existing = self._inflight.get(cache_key)
            if existing is None or existing.done():
                # 已结束的任务一律不复用。等待方被取消时（SSE 断开、上层超时都是
                # 常态），下面的 finally 往往在被 shield 的任务结束前就跑完了，
                # 于是摘不掉；等它随后失败就永久留在表里，把**上一次**的错误重放给
                # 后来者——目标服务可能早已恢复，真正的操作一次都不会执行。
                existing = asyncio.create_task(self._execute_with_retry(operation))
                self._inflight[cache_key] = existing

        try:
            value = await asyncio.shield(existing)
            self._store(cache_key, value, ttl_seconds)
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
