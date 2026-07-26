"""ResilientExecutor 的三个可靠性缺陷。

这一层是所有外部只读调用（高德天气、Tavily 搜索）的公共通道，
adapters/weather.py 和 adapters/search.py 都只经由它出网。三个问题都属于
"越是外部服务不稳定的时候越容易踩到"：

1. **失败的 inflight 任务不清理，会把旧错误重放给后来者。**
   execute 的 finally 只在 `existing.done()` 为真时才把任务从 _inflight 里摘掉。
   但等待方被取消时（SSE 连接断开、上层 asyncio.timeout 到点——都是常态），
   finally 跑的那一刻被 shield 保护的任务往往还没结束，于是不摘；等它随后失败，
   就永久留在 _inflight 里。下一个请同一个 key 的调用会直接 await 这个早已失败
   的任务，拿到**上一次**的错误，而目标服务此刻可能已经恢复了 —— 真正的操作
   一次都不会被执行，也没有任何重试。

2. **熔断器把新鲜缓存一起挡掉了。**
   _check_circuit 在读缓存之前执行。缓存的意义恰恰是在上游挂掉时还能答上来，
   而这里的顺序让熔断期间连"手上已有的、还没过期的答案"都返回不了。
   更糟的是熔断器是**全局**的：查天气失败三次，会让另一个 key 上完全健康、
   缓存还新鲜的搜索结果一起被拒。

3. **缓存只增不减。**
   过期条目从不回收，也没有条目上限，_cache 随不同 query 无上界增长。search 的
   cache_key 里带了完整 query 文本（tavily:{query}:{max_results}），用户输入的
   多样性直接变成常驻内存。修法是加条目上限并优先淘汰过期项，而不是每次写缓存
   都全量扫一遍（那是请求路径上的 O(n)）。
"""

import asyncio

import pytest

from app.mcp_core.reliability import (
    CircuitOpenError,
    ExternalServiceError,
    ResilientExecutor,
)


@pytest.mark.asyncio
async def test_cancelled_awaiter_does_not_poison_the_next_caller():
    """等待方被取消后，失败的 inflight 任务不能留下来重放给下一个调用者。"""
    executor = ResilientExecutor(timeout_seconds=5, max_retries=0, failure_threshold=99)

    async def failing():
        await asyncio.sleep(0.05)
        raise RuntimeError("transient blip")

    pending = asyncio.create_task(executor.execute("k", failing, ttl_seconds=60))
    await asyncio.sleep(0.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    # 给被 shield 的任务留出跑完并失败的时间。
    await asyncio.sleep(0.1)

    healthy_calls = 0

    async def healthy():
        nonlocal healthy_calls
        healthy_calls += 1
        return "FRESH"

    # 服务已恢复：新调用必须真的去调，而不是拿到上一次的陈旧错误。
    assert await executor.execute("k", healthy, ttl_seconds=60) == "FRESH"
    assert healthy_calls == 1


@pytest.mark.asyncio
async def test_finished_inflight_task_is_never_retained():
    """任何已结束的 inflight 任务都不该留在表里，无论成功还是失败。"""
    executor = ResilientExecutor(timeout_seconds=5, max_retries=0, failure_threshold=99)

    async def failing():
        raise RuntimeError("down")

    with pytest.raises(ExternalServiceError):
        await executor.execute("k", failing, ttl_seconds=60)

    async def good():
        return "ok"

    assert await executor.execute("other", good, ttl_seconds=60) == "ok"
    assert executor._inflight == {}


@pytest.mark.asyncio
async def test_fresh_cache_is_served_while_the_circuit_is_open():
    """熔断期间最该做的就是拿缓存兜底，而不是连缓存一起拒掉。"""
    executor = ResilientExecutor(
        timeout_seconds=5, max_retries=0, failure_threshold=1, reset_after_seconds=300
    )

    async def good():
        return "CACHED_OK"

    await executor.execute("weather:成都", good, ttl_seconds=600)

    async def bad():
        raise RuntimeError("upstream down")

    with pytest.raises(ExternalServiceError):
        await executor.execute("weather:重庆", bad, ttl_seconds=60)

    # 熔断已开启，但这个 key 的缓存还新鲜。
    assert await executor.execute("weather:成都", good, ttl_seconds=600) == "CACHED_OK"


@pytest.mark.asyncio
async def test_circuit_still_blocks_uncached_calls():
    """兜底不能变成绕过：没有缓存时熔断必须照旧拦住新的出网请求。"""
    executor = ResilientExecutor(
        timeout_seconds=5, max_retries=0, failure_threshold=1, reset_after_seconds=300
    )

    async def bad():
        raise RuntimeError("down")

    with pytest.raises(ExternalServiceError):
        await executor.execute("first", bad, ttl_seconds=60)

    calls = 0

    async def never_called():
        nonlocal calls
        calls += 1
        return "should not happen"

    with pytest.raises(CircuitOpenError):
        await executor.execute("second", never_called, ttl_seconds=60)
    assert calls == 0


@pytest.mark.asyncio
async def test_expired_entries_are_evicted_before_live_ones():
    """到了上限先扔过期的，别把还能用的答案扔掉而留着垃圾。

    这里不要求"过期即刻回收"：每次写缓存都全量扫一遍 dict 是请求路径上的
    O(n)，而内存的真正保证来自条目上限。要求的是淘汰顺序合理——
    留着过期垃圾却把新鲜答案挤出去，会让缓存在最需要它的时候失效。
    """
    executor = ResilientExecutor(timeout_seconds=5, max_retries=0)

    async def op():
        return "v"

    # 先塞满一批马上过期的，再塞满一批长期有效的。
    for index in range(_cap()):
        await executor.execute(f"stale{index}", op, ttl_seconds=0.001)
    await asyncio.sleep(0.05)
    for index in range(20):
        await executor.execute(f"live{index}", op, ttl_seconds=600)

    assert len(executor._cache) <= _cap()
    # 20 条新鲜的必须全都还在。
    assert all(f"live{index}" in executor._cache for index in range(20))


@pytest.mark.asyncio
async def test_cache_size_is_bounded_even_with_live_entries():
    """全都没过期时也要有上界，否则同样会被大量不同 query 撑爆。"""
    executor = ResilientExecutor(timeout_seconds=5, max_retries=0)

    async def op():
        return "v"

    for index in range(_over_cap()):
        await executor.execute(f"live{index}", op, ttl_seconds=600)

    assert len(executor._cache) <= _cap()


def _cap() -> int:
    from app.mcp_core.reliability import _MAX_CACHE_ENTRIES

    return _MAX_CACHE_ENTRIES


def _over_cap() -> int:
    return _cap() + 20


@pytest.mark.asyncio
async def test_over_cap_with_all_live_entries_evicts_the_soonest_to_expire():
    """全都没过期时按"最早到期"淘汰，别把最长寿、最可复用的那批先扔掉。

    上一个测试覆盖不到这条分支：那里过期项一清就已经降到上限以下了。
    """
    executor = ResilientExecutor(timeout_seconds=5, max_retries=0)

    async def op():
        return "v"

    # 长寿的先写入，短命的后写入；总数超过上限，强制走排序淘汰。
    for index in range(_cap()):
        await executor.execute(f"long{index}", op, ttl_seconds=6000)
    for index in range(20):
        await executor.execute(f"short{index}", op, ttl_seconds=600)

    assert len(executor._cache) <= _cap()
    # 被淘汰的应该是 short*（最早到期），long* 要留下来。
    assert not any(f"short{index}" in executor._cache for index in range(20))
    assert all(f"long{index}" in executor._cache for index in range(20))


@pytest.mark.asyncio
async def test_cached_value_is_still_returned_within_ttl():
    """回收逻辑不能顺手把没过期的值删掉——那会让缓存彻底失效。"""
    executor = ResilientExecutor(timeout_seconds=5, max_retries=0)
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        return "value"

    assert await executor.execute("key", op, ttl_seconds=600) == "value"
    assert await executor.execute("key", op, ttl_seconds=600) == "value"
    assert calls == 1
