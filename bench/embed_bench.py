"""Embedding latency and dimension for nomic-embed-text (NFR2.1 budget: query embed 10-40 ms)."""

import asyncio
import statistics
import time

from ragnarok.runtime.gateway import Gateway

QUERIES = [
    "search_query: what time do I prefer meetings",
    "search_query: why is test_refund_rounding failing",
    "search_query: what did I decide about the payments service last week",
    "search_query: port 8089 auth_middleware.py",
]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p * (len(xs) - 1)))]


async def main() -> None:
    gw = Gateway()
    try:
        t = time.perf_counter()
        first = await gw.embed(QUERIES[:1])
        print(f"cold first call: {(time.perf_counter() - t) * 1000:.0f} ms, shape {first.shape}")
        singles = []
        for i in range(60):
            t = time.perf_counter()
            await gw.embed([QUERIES[i % len(QUERIES)]])
            singles.append((time.perf_counter() - t) * 1000)
        print(
            f"single query: p50 {statistics.median(singles):.1f} ms, "
            f"p95 {pct(singles, 0.95):.1f} ms, max {max(singles):.1f} ms"
        )
        docs = [f"search_document: memory number {i} about project payments" for i in range(64)]
        t = time.perf_counter()
        batch = await gw.embed(docs)
        dt = (time.perf_counter() - t) * 1000
        print(f"batch of 64: {dt:.0f} ms ({dt / 64:.1f} ms each), dim {batch.shape[1]}")
    finally:
        await gw.aclose()


if __name__ == "__main__":
    asyncio.run(main())
