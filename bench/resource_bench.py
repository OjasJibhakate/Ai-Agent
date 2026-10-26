"""NFR2: how much of the machine the always-on part uses, and how fast the hot path is.

    uv run python bench/resource_bench.py            # 20 s of idle monitoring
    uv run python bench/resource_bench.py --seconds 60

Measured, with the real trigger engine on a throwaway vault:

- NFR2.2  background monitoring: CPU time used by this process (all threads) divided by wall time
          and by the machine's logical processors, while the engine watches 3 folders holding 300
          files each, runs 8 reminders and schedules, and looks for suggestions. Target: under 30%
          of the machine. Also the peak working set. (Model inference is measured separately by
          `bench/llm_bench.py` and `ollama ps`; it runs in Ollama, not in this process.)
- NFR2.1  intent classification (`looks_multi_step`) over 2,000 sentences, and memory retrieval
          with the vector index and full-text search over 500 memories. The query embedding is a
          call to the local embedding model: its latency is in bench/results/retrieval_*.json.

Results go to bench/results/resource_<date>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ragnarok.memory.service import MemoryService  # noqa: E402
from ragnarok.memory.store import MemoryStore  # noqa: E402
from ragnarok.memory.vault import Vault  # noqa: E402
from ragnarok.planning.intent import looks_multi_step  # noqa: E402
from ragnarok.planning.store import PlanStore  # noqa: E402
from ragnarok.triggers.engine import TriggerEngine  # noqa: E402
from ragnarok.triggers.proactive import Proactive  # noqa: E402
from ragnarok.triggers.store import TriggerStore  # noqa: E402
from tests.fake_embedder import FakeEmbedder  # noqa: E402

SENTENCES = [
    "find my notes, make slides, and save them",
    "what is 15 percent of 2400",
    "open steam",
    "first read the invoice then email the total and after that file it",
    "remind me to call mum at 6pm",
    "summarise this pdf",
] * 340
TARGET_CPU_PERCENT = 30.0
TARGET_LATENCY_MS = 800.0


def peak_working_set_mb() -> float | None:
    if sys.platform != "win32":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]  # fmt: skip

    info = Counters()
    info.cb = ctypes.sizeof(info)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
        ctypes.c_void_p(kernel32.GetCurrentProcess()), ctypes.byref(info), info.cb
    )
    return round(info.PeakWorkingSetSize / 1024**2, 1) if ok else None


async def monitor(seconds: float, workdir: Path) -> dict:
    key = os.urandom(32)
    vault = Vault.open(workdir / "vault.db", key)
    store = MemoryStore(vault)
    store.start()
    triggers = TriggerStore(vault)
    now = datetime.now()
    for n in range(5):
        triggers.add("cron", f"{n * 7 % 60} {8 + n} * * *", "notify", f"daily thing {n}", "daily")
    for n in range(3):
        triggers.add("date", str(now + timedelta(days=1 + n)), "notify", f"later {n}", "")
    for n in range(3):
        folder = workdir / f"watched{n}"
        folder.mkdir()
        for i in range(300):
            (folder / f"file{i}.txt").write_text("x")
        triggers.add("folder", str(folder), "notify", "new file", "files", glob="*.txt")
    proactive = Proactive(store, PlanStore(vault), triggers)
    engine = TriggerEngine(triggers, suggester=proactive.run_once, suggest_seconds=5.0)

    engine.start()
    await asyncio.sleep(3)  # let the first folder snapshots settle: this is steady state
    cpu0, wall0 = time.process_time(), time.perf_counter()
    await asyncio.sleep(seconds)
    cpu = time.process_time() - cpu0
    wall = time.perf_counter() - wall0
    await engine.stop()
    vault.close()
    cores = os.cpu_count() or 1
    return {
        "seconds": round(wall, 1),
        "logical_processors": cores,
        "cpu_percent_of_one_core": round(100 * cpu / wall, 2),
        "cpu_percent_of_machine": round(100 * cpu / wall / cores, 2),
        "peak_working_set_mb": peak_working_set_mb(),
    }


def intent_latency() -> dict:
    times = []
    for text in SENTENCES:
        start = time.perf_counter()
        looks_multi_step(text)
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    return {
        "sentences": len(times),
        "p50_ms": round(statistics.median(times), 4),
        "p95_ms": round(times[int(0.95 * (len(times) - 1))], 4),
    }


async def retrieval_latency(workdir: Path) -> dict:
    key = os.urandom(32)
    vault = Vault.open(workdir / "retrieval.db", key)
    store = MemoryStore(vault)
    store.start()
    svc = MemoryService(store, FakeEmbedder())
    topics = ["project", "meeting", "invoice", "travel", "recipe", "exam", "budget", "garden"]
    for i in range(500):
        topic = topics[i % len(topics)]
        await svc.remember(
            f"Note {i}: the {topic} number {i} was discussed on day {i % 28}", "semantic", "bench"
        )
    times = []
    for i in range(100):
        start = time.perf_counter()
        await svc.recall(f"what about the {topics[i % len(topics)]} number {i}", k=6)
        times.append((time.perf_counter() - start) * 1000)
    vault.close()
    times.sort()
    return {
        "memories": 500,
        "p50_ms": round(statistics.median(times), 2),
        "p95_ms": round(times[int(0.95 * (len(times) - 1))], 2),
        "note": "fake embedder: add the real query-embedding latency from retrieval_*.json",
    }


async def main_async(seconds: float) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        background = await monitor(seconds, work)
        retrieval = await retrieval_latency(work)
    intent = intent_latency()
    return {
        "date": date.today().isoformat(),
        "nfr2_2_background": background,
        "nfr2_1_intent": intent,
        "nfr2_1_retrieval": retrieval,
        "meets_nfr2_2": background["cpu_percent_of_machine"] < TARGET_CPU_PERCENT,
        "meets_nfr2_1_intent": intent["p95_ms"] < TARGET_LATENCY_MS,
        "meets_nfr2_1_retrieval_search": retrieval["p95_ms"] < TARGET_LATENCY_MS,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=20)
    args = parser.parse_args()
    result = asyncio.run(main_async(args.seconds))
    out = ROOT / "bench" / "results" / f"resource_{result['date']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0 if result["meets_nfr2_2"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
