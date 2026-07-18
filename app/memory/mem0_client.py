"""Mem0 语义经历适配器；延迟加载并允许无依赖降级。"""

from __future__ import annotations

from typing import Any, Protocol


class SemanticMemory(Protocol):
    async def add_confirmed(self, user_id: str, content: str, metadata: dict | None = None) -> Any: ...
    async def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]: ...
    async def delete(self, memory_id: str) -> Any: ...


class NullSemanticMemory:
    async def add_confirmed(self, user_id: str, content: str, metadata: dict | None = None):
        return {"status": "disabled"}

    async def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        return []

    async def delete(self, memory_id: str):
        return {"status": "disabled"}


class Mem0SemanticMemory:
    """官方 mem0ai AsyncMemory 的薄适配层。"""

    def __init__(self, config: dict | None = None):
        try:
            from mem0 import AsyncMemory
        except ImportError as exc:
            raise RuntimeError("未安装可选依赖，请运行 pip install -e .[memory]") from exc
        self.client = AsyncMemory(config) if config else AsyncMemory()

    async def add_confirmed(self, user_id: str, content: str, metadata: dict | None = None):
        return await self.client.add(
            [{"role": "user", "content": content}],
            user_id=user_id,
            metadata={"confirmed": True, **(metadata or {})},
            infer=False,
        )

    async def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        result = await self.client.search(query, user_id=user_id, limit=limit)
        return list(result.get("results", []))

    async def delete(self, memory_id: str):
        return await self.client.delete(memory_id)
