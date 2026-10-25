"""Tool definitions. Every tool declares schema, risk, idempotency, timeout, sandbox and its
fs/network needs (AGENTS.md hard rule 4). The description is what the model sees."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from ragnarok.memory.ingest import Ingestor
from ragnarok.memory.models import format_memory
from ragnarok.memory.service import MemoryService
from ragnarok.policy.permissions import Risk

# The user-message event of the turn being answered; set by the loop, read by memory_save.
CURRENT_EVENT: ContextVar[int | None] = ContextVar("current_event", default=None)
# The user message being answered; lets a tool tell a plain lookup from a bigger request.
CURRENT_TEXT: ContextVar[str] = ContextVar("current_text", default="")
MAX_RESULT_CHARS = 4000  # about 1,000 tokens; longer output is cut with a note


class ToolArgs(BaseModel):
    reason: str | None = Field(
        default=None, max_length=200, description="Why you are calling this, in a few words."
    )


class MemorySearchArgs(ToolArgs):
    query: str = Field(min_length=2, max_length=300, description="What to look for.")
    memory_type: Literal["episodic", "semantic", "preference", "procedural", "project"] | None = (
        None
    )
    since: str | None = Field(
        default=None, description="Only memories from this date on, YYYY-MM-DD."
    )
    project: str | None = None


class MemorySaveArgs(ToolArgs):
    content: str = Field(
        min_length=3, max_length=500, description="One short statement that names its subject."
    )
    memory_type: Literal["episodic", "semantic", "preference", "procedural", "project"] = "semantic"


@dataclass(frozen=True)
class ToolResult:
    """What a handler returns when its text already answers the user.

    `final=True` ends the turn without another model call (a second call just to say
    "Opened Steam" costs one to three seconds). Plain strings go back to the model as before.
    """

    text: str
    final: bool = False


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[ToolArgs]
    risk: Risk
    idempotent: bool
    timeout_s: float
    sandbox: str  # read-only | workspace-write | full-access
    handler: Callable[[Any], Awaitable[str | ToolResult]]
    fs: dict[str, list[str]] = field(default_factory=dict)
    network: list[str] = field(default_factory=list)

    def spec(self) -> dict[str, Any]:
        """The tool in Ollama's function-calling format."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        # Hidden from the model: asking it to explain each call cost about 19 generated tokens
        # (0.4 s) and it only restated the request. The trace still records tool, args and rule.
        schema["properties"].pop("reason", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


def memory_tools(svc: MemoryService, ingestor: Ingestor) -> list[Tool]:
    async def memory_search(args: MemorySearchArgs) -> str:
        hits = await svc.recall(args.query, k=8, project=args.project)
        hits = [
            h
            for h in hits
            if (not args.memory_type or h.memory.memory_type == args.memory_type)
            and (not args.since or h.memory.created_at[:10] >= args.since)
        ]
        if not hits:
            return "No matching memories."
        return "\n".join(format_memory(h.memory) for h in hits)

    async def memory_save(args: MemorySaveArgs) -> ToolResult:
        event = CURRENT_EVENT.get()
        action, memory_id = await ingestor.remember_explicit(
            args.content, args.memory_type, event_ids=[event] if event else []
        )
        said = {
            "ADD": "Saved",
            "UPDATE": "Already known",
            "NOOP": "Already known",
            "SUPERSEDE": "Updated",
        }[action]
        note = " (replaces the older version)" if action == "SUPERSEDE" else ""
        return ToolResult(f"{said}: {args.content} [m:{memory_id}]{note}", final=True)

    return [
        Tool(
            name="memory_search",
            description="Search what you remember about the user. Use it when the memory context "
            "does not already answer the question.",
            args_model=MemorySearchArgs,
            risk=Risk.R0,
            idempotent=True,
            timeout_s=10,
            sandbox="read-only",
            handler=memory_search,
        ),
        Tool(
            name="memory_save",
            description="Remember something for later. Use it when the user asks you to remember "
            "something or states a lasting preference or fact.",
            args_model=MemorySaveArgs,
            risk=Risk.R1,
            idempotent=True,  # duplicates are detected by content, so a retry can't double-save
            timeout_s=30,
            sandbox="workspace-write",
            handler=memory_save,
        ),
    ]
