"""How well does qwen3.5:4b extract memories and decide ADD / UPDATE / SUPERSEDE? (M3, real model)

Run: uv run python -m bench.ingest_eval
Writes bench/results/ingest_eval_<date>.json. Cases are synthetic and written by hand.
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from ragnarok.memory.ingest import Candidate, GatewayLlm, Ingestor
from ragnarok.memory.models import Memory
from ragnarok.runtime.gateway import Gateway

TODAY = "2026-10-25T12:00:00Z"

# (event text, regexes that must each match some extracted memory; [] means nothing should be kept)
EXTRACTION = [
    ("I prefer meetings after 2pm", [r"(14:00|2 ?pm|14h|2:00 ?pm)"]),
    ("My sister Meera moved to Bangalore last month", [r"Meera", r"Bangalore"]),
    ("thanks, that works", []),
    ("What's the weather like today?", []),
    (
        "Remind me that my dentist appointment is on 3 November at 10:30",
        [r"dentist", r"(3 November|November 3|2026-11-03|03 Nov)"],
    ),
    ("I'm allergic to penicillin", [r"penicillin"]),
    (
        "I started working on the billing service, it uses Python 3.12 and FastAPI",
        [r"billing", r"3\.12"],
    ),
    (
        "Tomorrow I have a flight to Delhi at 6am",
        [r"Delhi", r"(2026-10-26|26 October|October 26|Oct 26)"],
    ),
    ("I don't eat eggs, I'm vegetarian", [r"vegetarian"]),
    ("lol ok", []),
    ("Can you draft an email to Ravi about the budget?", []),
    ("Ravi is my manager and we have a 1:1 every Monday at 11", [r"Ravi", r"Monday"]),
    ("I hate it when people call me before 10am", [r"(10 ?am|10:00)"]),
    (
        "The test test_refund_rounding is failing on port 8089 in the payments service",
        [r"test_refund_rounding", r"8089"],
    ),
    ("My anniversary is on 14 February", [r"(2027-02-14|14 February|February 14|Feb 14|14 Feb)"]),
]

# (stored memories, new statement, acceptable actions)
DECISIONS = [
    (["Prefers meetings after 14:00"], "Prefers meetings after 15:00 now", {"SUPERSEDE"}),
    (["Manager is Anil"], "Manager is now Sunita", {"SUPERSEDE"}),
    (["Likes green tea"], "Likes coffee in the morning", {"ADD"}),
    (
        ["Allergic to penicillin"],
        "Allergic to penicillin antibiotics",
        {"UPDATE", "NOOP", "SUPERSEDE"},
    ),
    (["Lives in Pune"], "Lives in Bengaluru", {"SUPERSEDE"}),
    (["Sister Meera lives in Bangalore"], "Sister Meera is a doctor", {"ADD"}),
    (["Uses VS Code as the main editor"], "Switched to Neovim as the main editor", {"SUPERSEDE"}),
    (
        ["Dentist appointment on 3 November at 10:30"],
        "Dentist appointment is on 3 November at 10:30 AM",
        {"UPDATE", "NOOP"},
    ),
    (["Gym day is Tuesday"], "Reads on the Kindle before bed", {"ADD"}),
    (["Sprint length is two weeks"], "Sprints last 14 days", {"UPDATE", "NOOP"}),
    (["Phone is a Pixel 6"], "Bought a new iPhone 15 and uses it now", {"SUPERSEDE"}),
    (["Favourite language is Java"], "Also enjoys writing Go on weekends", {"ADD"}),
]


def memory(i: int, content: str) -> Memory:
    return Memory(
        id=i, content=content, memory_type="preference", origin="user_stated", confidence=0.95,
        importance=0.5, source="eval", project_id=None, sensitivity="normal", status="active",
        supersedes_id=None, valid_from=None, valid_to=None, pinned=False, created_at=TODAY,
        last_accessed_at=None, access_count=0,
    )  # fmt: skip


async def main() -> None:
    gw = Gateway(timeout=300)
    ing = Ingestor(None, None, GatewayLlm(gw))  # type: ignore[arg-type]  # store/embedder unused here
    results: dict = {"date": datetime.now(UTC).isoformat(timespec="seconds"), "model": "qwen3.5:4b"}
    try:
        await ing._extract([{"id": 0, "kind": "user_message", "ts": TODAY, "payload": "warm up"}])

        print("== extraction ==")
        rows, lat = [], []
        for n, (text, must) in enumerate(EXTRACTION, 1):
            event = {"id": n, "kind": "user_message", "ts": TODAY, "payload": text}
            t = time.perf_counter()
            got: list[Candidate] = await ing._extract([event])
            lat.append((time.perf_counter() - t) * 1000)
            blob = " | ".join(c.content for c in got)
            if must:
                ok = bool(got) and all(re.search(p, blob, re.I) for p in must)
            else:
                ok = not got
            rows.append({"text": text, "ok": ok, "got": [c.content for c in got]})
            print(f"{'PASS' if ok else 'FAIL'}  {text!r} -> {blob or '(nothing)'}")
        keep = [r for r, (_, must) in zip(rows, EXTRACTION, strict=True) if must]
        skip = [r for r, (_, must) in zip(rows, EXTRACTION, strict=True) if not must]
        results["extraction"] = {
            "accuracy": statistics.mean(r["ok"] for r in rows),
            "kept_correctly": statistics.mean(r["ok"] for r in keep),
            "skipped_correctly": statistics.mean(r["ok"] for r in skip),
            "latency_ms": {"p50": statistics.median(lat), "max": max(lat)},
            "cases": rows,
        }

        print("\n== decisions ==")
        rows, lat = [], []
        for stored, new, accept in DECISIONS:
            cand = Candidate(content=new, memory_type="preference")
            t = time.perf_counter()
            d = await ing._decide(cand, [memory(i + 1, s) for i, s in enumerate(stored)])
            lat.append((time.perf_counter() - t) * 1000)
            ok = d.action in accept and (d.action in ("ADD", "NOOP") or d.target_id == 1)
            rows.append(
                {"stored": stored, "new": new, "action": d.action, "target": d.target_id, "ok": ok}
            )
            print(
                f"{'PASS' if ok else 'FAIL'}  {stored[0]!r} + {new!r} -> {d.action} {d.target_id}"
            )
        results["decisions"] = {
            "accuracy": statistics.mean(r["ok"] for r in rows),
            "latency_ms": {"p50": statistics.median(lat), "max": max(lat)},
            "cases": rows,
        }
    finally:
        await gw.aclose()

    ex, de = results["extraction"], results["decisions"]
    print(
        f"\nextraction {ex['accuracy']:.2f} (keep {ex['kept_correctly']:.2f}, skip {ex['skipped_correctly']:.2f}), "
        f"p50 {ex['latency_ms']['p50']:.0f} ms | decisions {de['accuracy']:.2f}, p50 {de['latency_ms']['p50']:.0f} ms"
    )
    out = Path("bench/results") / f"ingest_eval_{datetime.now(UTC):%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
