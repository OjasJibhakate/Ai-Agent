"""End-to-end memory demo on the real models (M4): tell it things, restart, come back the next day.

Run: uv run python -m bench.m4_demo
Uses a throwaway vault in a temp folder and a fake clock for "the next day". Writes
bench/results/m4_demo_<date>.json. Nothing here touches %LOCALAPPDATA%\\Ragnarok.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ragnarok.memory.ingest import GatewayLlm, Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.runtime.gateway import Gateway
from ragnarok.runtime.loop import Agent, Session

START = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


async def open_agent(path: Path, key: bytes, gw: Gateway, clock: Clock):
    vault = Vault.open(path, key)
    store = MemoryStore(vault, clock=clock)
    store.start()
    svc = MemoryService(store, gw)
    ingestor = Ingestor(store, gw, GatewayLlm(gw))
    return vault, store, ingestor, Agent(store, svc, ingestor, gw)


async def main() -> None:
    gw = Gateway(timeout=300)
    clock = Clock()
    key = os.urandom(32)
    rows, checks = [], []

    async def say(agent: Agent, session: Session, text: str, expect: str | None = None) -> None:
        result = await agent.turn(session, text)
        tools = [f"{t.tool}({json.dumps(t.args)})" for t in result.trace]
        ok = None if expect is None else bool(re.search(expect, result.text, re.I))
        print(f"\nYOU: {text}\n  tools: {tools or '-'}\n  AI : {result.text}")
        print(
            f"  recall {result.recall_ms:.0f} ms | first token {result.first_token_ms or 0:.0f} ms | "
            f"total {result.total_ms:.0f} ms | steps {result.steps}"
            + ("" if ok is None else f" | {'PASS' if ok else 'FAIL'} /{expect}/")
        )
        rows.append(
            {"you": text, "ai": result.text, "tools": tools, "recall_ms": result.recall_ms,
             "first_token_ms": result.first_token_ms, "total_ms": result.total_ms, "ok": ok}
        )  # fmt: skip
        if ok is not None:
            checks.append(ok)

    try:
        print(f"warm-up: {await gw.warm_up():.1f} s")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.db"

            print("\n===== day 1, session A =====")
            vault, store, ingestor, agent = await open_agent(path, key, gw, clock)
            a = Session()
            await say(agent, a, "Hi! Please remember that I prefer meetings after 2pm.")
            await say(agent, a, "My manager is Ravi and we have a one on one every Monday at 11.")
            await say(agent, a, "Thanks, that's all for now.")
            report = await ingestor.ingest_pending()
            print(f"\nend of session: {report}")
            print("memories:", [m.content for m in store.list_memories()])
            vault.close()  # the process "dies"

            print("\n===== day 2, session B (after a restart) =====")
            clock.now = START + timedelta(days=1, hours=2)
            vault, store, ingestor, agent = await open_agent(path, key, gw, clock)
            b = Session()
            await say(agent, b, "When should we schedule the team retro?", r"(14:00|2 ?pm|2:00|after 2)")
            await say(agent, b, "Who is my manager?", r"Ravi")
            await say(agent, b, "Actually, I prefer meetings after 3pm from now on.")
            report = await ingestor.ingest_pending()
            print(f"\nend of session: {report}")
            vault.close()

            print("\n===== day 3, session C =====")
            clock.now = START + timedelta(days=2, hours=1)
            vault, store, ingestor, agent = await open_agent(path, key, gw, clock)
            await say(agent, Session(), "What time do I prefer for meetings?", r"(15:00|3 ?pm|3:00)")
            active = [m.content for m in store.list_memories()]
            history = vault.query("SELECT content, status FROM memories ORDER BY id")
            print("\nactive memories:", active)
            print("all (with status):", history)
            stale_active = [c for c in active if re.search(r"2 ?pm|14:00", c)]
            checks.append(not stale_active)
            print(f"old 2pm preference still active: {bool(stale_active)} -> {'FAIL' if stale_active else 'PASS'}")
            vault.close()
    finally:
        await gw.aclose()

    print(f"\n{sum(checks)}/{len(checks)} checks passed")
    out = Path("bench/results") / f"m4_demo_{datetime.now(UTC):%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"turns": rows, "checks": checks}, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
