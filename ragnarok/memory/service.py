"""Async entry point to memory: embeds through the gateway, runs the blocking store in a thread."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from ragnarok.memory.models import MemoryHit, MemoryType, Origin, Sensitivity
from ragnarok.memory.store import MemoryStore
from ragnarok.runtime.gateway import GatewayError

DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> np.ndarray: ...


class MemoryService:
    def __init__(self, store: MemoryStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder
        self._background: set[asyncio.Task] = set()

    async def start(self) -> str:
        return await asyncio.to_thread(self.store.start)

    async def remember(
        self,
        content: str,
        memory_type: MemoryType,
        source: str,
        *,
        origin: Origin = "user_stated",
        importance: float = 0.5,
        project: str | None = None,
        sensitivity: Sensitivity = "normal",
        pinned: bool = False,
        entities: Sequence[tuple[str, str]] = (),
        tags: Sequence[str] = (),
        event_ids: Sequence[int] = (),
    ) -> int:
        vector = (await self.embedder.embed([DOC_PREFIX + content]))[0]
        return await asyncio.to_thread(
            self.store.add_memory,
            content,
            vector,
            memory_type=memory_type,
            source=source,
            origin=origin,
            importance=importance,
            project=project,
            sensitivity=sensitivity,
            pinned=pinned,
            entities=entities,
            tags=tags,
            event_ids=event_ids,
        )

    async def recall(
        self, query: str, k: int = 8, *, project: str | None = None
    ) -> list[MemoryHit]:
        try:
            vector = (await self.embedder.embed([QUERY_PREFIX + query]))[0]
        except GatewayError:
            vector = None  # embedder down: lexical-only results beat no results (NFR3.1)
        hits = await asyncio.to_thread(self.store.search, query, vector, k, project=project)
        if hits:
            task = asyncio.create_task(
                asyncio.to_thread(self.store.touch, [h.memory.id for h in hits])
            )
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        return hits

    async def flush(self) -> None:
        """Wait for background bookkeeping (access counts). Call before closing the vault."""
        await asyncio.gather(*self._background)
