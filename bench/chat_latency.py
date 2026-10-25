"""How long does a chat turn take on the real models? (the "make it fast" benchmark)

Run: uv run python -m bench.chat_latency [rounds]
Four typical turns (greeting, remember a fact, open an app with a fake launcher, answer from memory),
repeated; reports the median per turn. Writes bench/results/chat_latency_<date>.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ragnarok.memory.ingest import GatewayLlm, Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.runtime.gateway import Gateway
from ragnarok.runtime.loop import Agent, Session
from ragnarok.runtime.tools import memory_tools
from ragnarok.skills.apps import AppEntry, app_tools

TURNS = [
    ("greeting", "yo yo"),
    ("remember a fact", "My favourite colour is green, please remember that."),
    ("open an app", "open steam"),
    ("answer from memory", "What is my favourite colour?"),
]


class FakeCatalog:
    """Resolves apps like the real one but never launches anything."""

    def list_apps(self):
        return [AppEntry("Steam", "steam"), AppEntry("Microsoft Edge", "MSEdge")]

    def launch(self, app) -> None:
        pass


async def main(rounds: int) -> None:
    gw = Gateway(timeout=300)
    results: dict[str, list[dict]] = {name: [] for name, _ in TURNS}
    try:
        print(f"warm-up {await gw.warm_up():.1f}s")
        with tempfile.TemporaryDirectory() as d:
            vault = Vault.open(Path(d) / "vault.db", os.urandom(32))
            store = MemoryStore(vault)
            store.start()
            svc = MemoryService(store, gw)
            ingestor = Ingestor(store, gw, GatewayLlm(gw))
            tools = memory_tools(svc, ingestor) + app_tools(FakeCatalog())
            agent = Agent(store, svc, ingestor, gw, tools=tools)
            for r in range(rounds):
                session = Session()
                for name, text in TURNS:
                    res = await agent.turn(session, text)
                    results[name].append(
                        {"total_ms": res.total_ms, "first_token_ms": res.first_token_ms or 0,
                         "steps": res.steps, "tokens": getattr(res, "tokens", 0), "text": res.text}
                    )  # fmt: skip
                    if r == 0:
                        print(f"  {name:20s} -> {res.text!r}")
            vault.close()
    finally:
        await gw.aclose()

    print(
        f"\n{'turn':20s} {'total ms':>9s} {'first tok':>10s} {'model calls':>12s} {'out tokens':>11s}"
    )
    summary = {}
    for name, rows in results.items():
        tot = statistics.median(x["total_ms"] for x in rows)
        ft = statistics.median(x["first_token_ms"] for x in rows)
        steps = statistics.median(x["steps"] for x in rows)
        toks = statistics.median(x["tokens"] for x in rows)
        summary[name] = {
            "total_ms": tot,
            "first_token_ms": ft,
            "model_calls": steps,
            "tokens": toks,
        }
        print(f"{name:20s} {tot:9.0f} {ft:10.0f} {steps:12.0f} {toks:11.0f}")
    out = Path("bench/results") / f"chat_latency_{datetime.now(UTC):%Y-%m-%d_%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "runs": results}, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
