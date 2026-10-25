"""Typed records returned by the memory layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

MemoryType = Literal["episodic", "semantic", "preference", "procedural", "project", "summary"]
Origin = Literal["user_stated", "observed", "inferred"]
Status = Literal["active", "archived", "superseded"]
Sensitivity = Literal["normal", "private", "secret"]


class Memory(BaseModel):
    id: int
    content: str
    memory_type: MemoryType
    origin: Origin
    confidence: float
    importance: float
    source: str
    project_id: int | None
    sensitivity: Sensitivity
    status: Status
    supersedes_id: int | None
    valid_from: str | None
    valid_to: str | None
    pinned: bool
    created_at: str
    last_accessed_at: str | None
    access_count: int


class MemoryHit(BaseModel):
    memory: Memory
    lexical_rank: int | None  # 1-based rank in the FTS5 list, None if absent
    semantic_rank: int | None  # 1-based rank in the vector list, None if absent
    rrf: float
    score: float  # final score after reranking
