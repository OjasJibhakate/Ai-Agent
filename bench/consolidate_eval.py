"""Does qwen3.5:4b write summaries that survive our validation? (M5, real model)

Run: uv run python -m bench.consolidate_eval
Four hand-written weeks of episodic notes. For each: the summary the model proposed, whether code
accepted it, and why not if rejected. Writes bench/results/consolidate_eval_<date>.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ragnarok.memory.consolidate import SUMMARY_SCHEMA, Consolidator, novel_terms, validate
from ragnarok.memory.ingest import GatewayLlm
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.runtime.gateway import Gateway

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
WEEKS = [
    [
        "Met Priya to plan the budget for the payments project",
        "Priya sent the first draft of the payments budget",
        "Reviewed the payments budget draft with Priya and asked for cuts",
        "The payments budget review moved to Friday",
        "Priya approved the revised payments budget on Friday",
    ],
    [
        "Booked a trip to Goa for the first weekend of October",
        "Booked a hotel near Calangute beach in Goa",
        "Packed the camera and the blue backpack for the Goa trip",
        "Rented a scooter in Goa for two days",
    ],
    [
        "The test test_refund_rounding failed on the payments service",
        "Found that refund rounding used floor instead of round half up",
        "Fixed the rounding in the refund calculator and added a test",
        "The payments service tests passed after the rounding fix",
        "Deployed the refund rounding fix to staging",
    ],
    [
        "Dentist visit for a routine cleaning",
        "Bought a new toothbrush and floss",
        "Started the 30 day flossing habit",
        "Dentist said the gums look healthy",
    ],
]


async def main() -> None:
    gw = Gateway(timeout=300)
    rows = []
    try:
        await gw.warm_up()
        with tempfile.TemporaryDirectory() as d:
            vault = Vault.open(Path(d) / "vault.db", os.urandom(32))
            clock_now = [NOW]
            store = MemoryStore(vault, clock=lambda: clock_now[0])
            store.start()
            for w, notes in enumerate(WEEKS):
                for i, text in enumerate(notes):
                    clock_now[0] = NOW - timedelta(days=60 + 7 * w - i * 0.2)
                    vec = (await gw.embed(["search_document: " + text]))[0]
                    store.add_memory(text, vec, memory_type="episodic", source="eval")
            clock_now[0] = NOW
            cons = Consolidator(store, gw, GatewayLlm(gw))
            groups, small = cons.plan()
            print(f"{len(groups)} groups planned, {small} too small\n")
            for g in groups:
                ids = {m.id for m in g.members}
                listing = "\n".join(f"[m:{m.id} {m.created_at[:10]}] {m.content}" for m in g.members)
                task = cons._task.replace("{memories}", listing)
                data = await cons.llm.json(
                    [{"role": "system", "content": cons._system}, {"role": "user", "content": task}],
                    SUMMARY_SCHEMA,
                )
                source = " ".join(m.content for m in g.members if m.id in set(data["cited_ids"]))
                reason = validate(data["summary"], data["cited_ids"], ids, source)
                rows.append(
                    {"summary": data["summary"], "cited": len(set(data["cited_ids"])),
                     "of": len(ids), "rejected": reason, "novel": novel_terms(data["summary"], source)}
                )  # fmt: skip
                verdict = "ACCEPT" if reason is None else f"REJECT ({reason}: {rows[-1]['novel']})"
                print(f"{verdict}\n  {data['summary']}\n  cites {rows[-1]['cited']} of {rows[-1]['of']}\n")
            vault.close()
    finally:
        await gw.aclose()
    accepted = sum(r["rejected"] is None for r in rows)
    print(f"{accepted}/{len(rows)} summaries accepted by validation")
    out = Path("bench/results") / f"consolidate_eval_{datetime.now(UTC):%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
