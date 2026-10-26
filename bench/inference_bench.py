"""NFR2.2 for local inference: how much of the machine the model uses while it answers, and while idle.

    uv run python bench/inference_bench.py            # 10 s idle, then 6 real chat turns

`bench/resource_bench.py` measures the always-on engine. This measures the other half of the
requirement: "local model inference". Whole-machine CPU (all 12 logical processors, so it includes
Ollama and everything else running) and the GPU (`nvidia-smi`) are sampled twice a second:

- idle: the model is loaded and resident, nothing is asked;
- active: six real chat turns through the gateway, back to back.

It also reports a duty cycle: the share of a working hour the model would be busy if the owner sent
one request a minute, so a burst of high use is not mistaken for a sustained load.

Results go to bench/results/inference_<date>.json.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ragnarok.runtime.gateway import Gateway  # noqa: E402

PROMPTS = [
    "In three sentences, explain what a circuit breaker does in software.",
    "Write a short Python function that returns the n-th Fibonacci number, with a one-line comment.",
    "List five things to check before sending an important email.",
    "Summarise in two sentences why local AI models are good for privacy.",
    "What is 15 percent of 2400? Answer with the number and one sentence.",
    "Give me a plan, in four short steps, for tidying a Downloads folder.",
]
TARGET = 30.0


class _FileTime(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]


def _ticks(ft: _FileTime) -> int:
    return (ft.hi << 32) | ft.lo


def cpu_times() -> tuple[int, int]:
    """(busy, total) 100 ns ticks across all processors since boot."""
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    )  # type: ignore[attr-defined]
    total = _ticks(kernel) + _ticks(user)  # kernel time already includes idle time
    return total - _ticks(idle), total


class _Entry(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_ulong), ("usage", ctypes.c_ulong), ("pid", ctypes.c_ulong),
        ("heap", ctypes.c_size_t), ("module", ctypes.c_ulong), ("threads", ctypes.c_ulong),
        ("parent", ctypes.c_ulong), ("priority", ctypes.c_long), ("flags", ctypes.c_ulong),
        ("exe", ctypes.c_wchar * 260),
    ]  # fmt: skip


def ollama_pids() -> list[int]:
    """Process ids of Ollama and its model runners (`ollama.exe`, `llama-server.exe`)."""
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    entry, pids = _Entry(), []
    entry.size = ctypes.sizeof(entry)
    ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
    while ok:
        if entry.exe.lower() in ("ollama.exe", "llama-server.exe", "ollama app.exe"):
            pids.append(entry.pid)
        ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    k32.CloseHandle(ctypes.c_void_p(snap))
    return pids


def process_ticks(pids: list[int]) -> int:
    """CPU time (100 ns ticks, kernel + user) used so far by these processes."""
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k32.OpenProcess.restype = ctypes.c_void_p
    total = 0
    for pid in pids:
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        if k32.GetProcessTimes(
            ctypes.c_void_p(handle), ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user),
        ):  # fmt: skip
            total += _ticks(kernel) + _ticks(user)
        k32.CloseHandle(ctypes.c_void_p(handle))
    return total


def gpu_sample() -> tuple[float, float] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()[0]  # fmt: skip
        util, mem = (float(x) for x in out.split(","))
        return util, mem
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


class Sampler(threading.Thread):
    def __init__(self, every: float = 0.5) -> None:
        super().__init__(daemon=True)
        self.every, self.cpu, self.gpu, self.mem, self.ollama = every, [], [], [], []
        self.pids = ollama_pids()
        self._halt = threading.Event()

    def run(self) -> None:
        busy0, total0 = cpu_times()
        own0 = process_ticks(self.pids)
        while not self._halt.wait(self.every):
            busy1, total1 = cpu_times()
            own1 = process_ticks(self.pids)
            if total1 > total0:
                self.cpu.append(100 * (busy1 - busy0) / (total1 - total0))
                self.ollama.append(100 * (own1 - own0) / (total1 - total0))  # total counts all CPUs
            busy0, total0, own0 = busy1, total1, own1
            sample = gpu_sample()
            if sample:
                self.gpu.append(sample[0])
                self.mem.append(sample[1])

    def finish(self) -> dict:
        self._halt.set()
        self.join()

        def stats(xs):
            return (
                {
                    "mean": round(statistics.fmean(xs), 1),
                    "peak": round(max(xs), 1),
                    "samples": len(xs),
                }
                if xs
                else None
            )

        return {"whole_machine_cpu_percent": stats(self.cpu), "ollama_cpu_percent_of_machine": stats(self.ollama), "gpu_percent": stats(self.gpu),
                "gpu_memory_mb": stats(self.mem)}  # fmt: skip


async def main_async(idle_seconds: float) -> dict:
    gw = Gateway(timeout=300)
    await gw.warm_up()  # the model is resident before anything is measured
    await asyncio.sleep(2)

    idle = Sampler()
    idle.start()
    await asyncio.sleep(idle_seconds)
    idle_stats = idle.finish()

    active = Sampler()
    active.start()
    busy_seconds, tokens = [], 0
    for prompt in PROMPTS:
        start = time.perf_counter()
        reply = await gw.chat([{"role": "user", "content": prompt}])
        busy_seconds.append(time.perf_counter() - start)
        tokens += reply.eval_count
    active_stats = active.finish()

    per_request = statistics.fmean(busy_seconds)
    duty = 100 * per_request / 60  # one request a minute
    return {
        "date": date.today().isoformat(),
        "logical_processors": os.cpu_count(),
        "idle_model_resident": idle_stats,
        "active_generating": active_stats,
        "turns": len(PROMPTS),
        "seconds_per_turn": round(per_request, 2),
        "tokens_generated": tokens,
        "duty_cycle_percent_at_one_request_a_minute": round(duty, 1),
        "target_percent": TARGET,
    }


def main() -> int:
    idle = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    result = asyncio.run(main_async(idle))
    out = ROOT / "bench" / "results" / f"inference_{result['date']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
