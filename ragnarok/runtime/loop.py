"""The agent loop: a model in a tool-calling loop, with permissions and a log (ARCHITECTURE 2.2).

Per turn: log the user message, retrieve memory (no model call), build the prompt, then let the
model call tools until it answers. Every tool call is validated, checked against the permission
profile, and written to the event log *before* it runs.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from ragnarok.memory.ingest import Ingestor
from ragnarok.memory.models import MemoryHit
from ragnarok.memory.profile import ProfileCache
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.policy import permissions
from ragnarok.runtime.context import build_messages
from ragnarok.runtime.gateway import ChatResult, GatewayError
from ragnarok.runtime.tools import MAX_RESULT_CHARS, Tool, memory_tools

MAX_STEPS = 6  # model calls per user turn
LOOP_GUARD = 3  # the same call with the same arguments this many times stops the turn

Approver = Callable[[str, dict[str, Any], permissions.Decision], Awaitable[bool]]


class ChatGateway(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ChatResult: ...


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ToolTrace:
    tool: str
    args: dict[str, Any]
    rule: str
    allowed: bool
    result: str


@dataclass
class TurnResult:
    text: str
    hits: list[MemoryHit] = field(default_factory=list)
    trace: list[ToolTrace] = field(default_factory=list)
    steps: int = 0
    stopped: str | None = None  # loop-guard | step-cap | model-error
    recall_ms: float = 0.0
    first_token_ms: float | None = None
    total_ms: float = 0.0


class Agent:
    def __init__(
        self,
        store: MemoryStore,
        svc: MemoryService,
        ingestor: Ingestor,
        gateway: ChatGateway,
        *,
        profile: str = "interactive",
        approve: Approver | None = None,
        tools: list[Tool] | None = None,
    ) -> None:
        self.store = store
        self.svc = svc
        self.gateway = gateway
        self.profile_name = profile
        self.approve = approve
        self.tools = {t.name: t for t in (tools or memory_tools(svc, ingestor))}
        self.profile = ProfileCache(store)

    async def turn(
        self,
        session: Session,
        text: str,
        *,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> TurnResult:
        t_start = time.perf_counter()
        await self._log(session, "user_message", text)

        # hot path: retrieval with no model call besides the query embedding
        t = time.perf_counter()
        hits = await self.svc.recall(text, k=6)
        profile_text, profile_ids = await asyncio.to_thread(self.profile.get)
        result = TurnResult(text="", hits=hits, recall_ms=(time.perf_counter() - t) * 1000)

        messages: list[dict[str, Any]] = build_messages(
            profile_text, session.history, text, hits, profile_ids
        )
        specs = [t.spec() for t in self.tools.values()]  # frozen for the whole turn
        seen: Counter[str] = Counter()

        for _ in range(MAX_STEPS):
            result.steps += 1
            try:
                reply = await self.gateway.chat(messages, tools=specs, on_delta=on_delta)
            except GatewayError:
                result.stopped = "model-error"
                result.text = "I can't reach my language model right now. Your message was saved."
                break
            if result.first_token_ms is None:
                result.first_token_ms = reply.first_token_ms
            if not reply.tool_calls:
                result.text = reply.content.strip()
                break
            messages.append(
                {"role": "assistant", "content": reply.content, "tool_calls": reply.tool_calls}
            )
            guarded = False
            for call in reply.tool_calls:
                name, args = _parse_call(call)
                key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                seen[key] += 1
                if seen[key] >= LOOP_GUARD:
                    guarded = True
                    break
                output = await self._run_tool(session, name, args, result)
                messages.append({"role": "tool", "tool_name": name, "content": output})
            if guarded:
                result.stopped = "loop-guard"
                result.text = "I stopped because I kept repeating the same step."
                break
        else:
            result.stopped = "step-cap"
            result.text = result.text or "I stopped after too many steps."

        await self._log(session, "assistant_message", result.text)
        session.history += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": result.text},
        ]
        result.total_ms = (time.perf_counter() - t_start) * 1000
        return result

    # ---- tools -------------------------------------------------------------------------------

    async def _run_tool(
        self, session: Session, name: str, args: dict[str, Any], result: TurnResult
    ) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"error: unknown tool {name!r}"
        try:
            parsed = tool.args_model.model_validate(args)
        except ValidationError as exc:
            msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            result.trace.append(ToolTrace(name, args, "invalid_args", False, msg))
            return f"error: invalid_args: {msg}"

        decision = permissions.check(self.profile_name, name, tool.risk)
        clean = parsed.model_dump(exclude={"reason"}, exclude_none=True)
        if decision.ask:
            approved = bool(self.approve and await self.approve(name, clean, decision))
            decision = permissions.Decision(approved, False, decision.rule, "asked the user")
        if not decision.allowed:
            await self._log(
                session, "memory_tool_call", _event(name, clean, parsed.reason, decision, "denied")
            )
            result.trace.append(ToolTrace(name, clean, decision.rule, False, "denied"))
            return f"error: denied by rule {decision.rule}"

        # log before acting: the call is durable before any side effect happens
        await self._log(
            session, "memory_tool_call", _event(name, clean, parsed.reason, decision, "started")
        )
        try:
            output = await asyncio.wait_for(tool.handler(parsed), tool.timeout_s)
        except TimeoutError:
            output = "error: timeout"
        except Exception as exc:  # a tool failure must not kill the turn
            output = f"error: {type(exc).__name__}"
        if len(output) > MAX_RESULT_CHARS:
            output = output[:MAX_RESULT_CHARS] + "\n[truncated]"
        await self._log(session, "memory_tool_result", f"{name}: {output}")
        result.trace.append(ToolTrace(name, clean, decision.rule, True, output))
        return output

    async def _log(self, session: Session, kind: str, payload: str) -> None:
        # tool events are never merged by the dedupe window: each call is its own record
        dedupe = 300 if kind in ("user_message", "assistant_message") else None
        await asyncio.to_thread(
            self.store.record_event,
            "chat",
            kind,
            payload,
            session_id=session.id,
            dedupe_seconds=dedupe,
        )


def _parse_call(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fn = call.get("function", {})
    args = fn.get("arguments") or {}
    if isinstance(args, str):  # some models send arguments as a JSON string
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return str(fn.get("name", "")), args if isinstance(args, dict) else {}


def _event(
    name: str, args: dict[str, Any], reason: str | None, d: permissions.Decision, status: str
) -> str:
    return json.dumps(
        {"tool": name, "args": args, "reason": reason, "rule": d.rule, "status": status},
        default=str,
    )
