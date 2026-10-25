"""Retrieval benchmark with real nomic-embed-text vectors (docs/MEMORY.md sections 8 and 10).

Run: uv run python -m bench.retrieval_bench
Writes bench/results/retrieval_<date>.json. All data is synthetic (bench/retrieval_data.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from bench.retrieval_data import Query, build
from ragnarok.memory.index import ExactIndex, TurbovecIndex
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.runtime.gateway import Gateway

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
GOLD_CATEGORIES = ("paraphrase", "exact", "temporal", "contradicted", "entity")
SWEEP = (0.0, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, round(p * (len(s) - 1)))]


async def embed_all(items, queries) -> tuple[np.ndarray, np.ndarray, list[float]]:
    gw = Gateway(timeout=300)
    try:
        await gw.embed(["search_query: warm up"])
        docs = []
        for i in range(0, len(items), 64):
            batch = [f"search_document: {it.text}" for it in items[i : i + 64]]
            docs.append(await gw.embed(batch))
        qvecs, lat = [], []
        for q in queries:
            t = time.perf_counter()
            qvecs.append((await gw.embed([f"search_query: {q.text}"]))[0])
            lat.append((time.perf_counter() - t) * 1000)
        return np.vstack(docs), np.stack(qvecs), lat
    finally:
        await gw.aclose()


def build_store(items, doc_vecs, index_kind: str, tmp: Path, min_semantic: float):
    vault = Vault.open(tmp / f"vault_{index_kind}.db", os.urandom(32))
    clock = Clock()
    store = MemoryStore(vault, index_kind=index_kind, clock=clock, min_semantic=min_semantic)
    store.start()
    ids: dict[str, int] = {}
    for it, vec in zip(items, doc_vecs, strict=True):
        clock.now = NOW - timedelta(days=it.age_days)
        ids[it.key] = store.add_memory(
            it.text,
            vec,
            memory_type=it.mtype,
            source="bench",
            entities=it.entities,
            supersedes=ids[it.supersedes] if it.supersedes else None,
        )
        if it.deleted:
            store.delete(ids[it.key])
    clock.now = NOW
    return vault, store, ids


def evaluate(store, ids, queries, qvecs, **search_kw) -> dict:
    key_of = {v: k for k, v in ids.items()}
    per: dict[str, dict[str, list[float]]] = {}
    latencies = []
    for q, vec in zip(queries, qvecs, strict=True):
        t = time.perf_counter()
        hits = store.search(q.text, vec, k=10, **search_kw)
        latencies.append((time.perf_counter() - t) * 1000)
        keys = [key_of[h.memory.id] for h in hits]
        m = per.setdefault(q.category, {"recall5": [], "mrr": [], "forbidden": [], "nonempty": []})
        m["forbidden"].append(float(any(f in keys[:10] for f in q.forbidden)))
        m["nonempty"].append(float(bool(keys)))
        if q.gold:
            first = next((r for r, k in enumerate(keys, 1) if k in q.gold), None)
            m["mrr"].append(1 / first if first else 0.0)
            if q.gold_all:
                m["recall5"].append(len(set(keys[:5]) & set(q.gold)) / len(q.gold))
            else:
                m["recall5"].append(float(first is not None and first <= 5))
    out = {"latency_ms": {"p50": statistics.median(latencies), "p95": pct(latencies, 0.95)}}
    for cat, m in per.items():
        out[cat] = {name: statistics.mean(vals) for name, vals in m.items() if vals}
    gold = [c for c in GOLD_CATEGORIES if c in out]
    out["gold_recall5"] = statistics.mean(out[c]["recall5"] for c in gold)
    out["gold_mrr"] = statistics.mean(out[c]["mrr"] for c in gold)
    out["forbidden_rate"] = statistics.mean(
        out[c]["forbidden"] for c in ("contradicted", "deleted") if c in out
    )
    out["false_positive_rate"] = statistics.mean(
        out[c]["nonempty"] for c in ("no_answer", "deleted") if c in out
    )
    return out


def fmt(row: dict) -> str:
    return (
        f"recall@5 {row['gold_recall5']:.3f} | MRR {row['gold_mrr']:.3f} | "
        f"forbidden {row['forbidden_rate']:.3f} | answers-when-none {row['false_positive_rate']:.2f} | "
        f"search p50 {row['latency_ms']['p50']:.1f} ms p95 {row['latency_ms']['p95']:.1f} ms"
    )


def index_comparison(items, doc_vecs, queries: list[Query], qvecs) -> dict:
    """Turbovec recall@10 against exact float32 search over the same real vectors."""
    ids = np.arange(1, len(doc_vecs) + 1, dtype=np.uint64)
    exact = ExactIndex(doc_vecs.shape[1])
    exact.add(ids, doc_vecs)
    result = {}
    for bits in (4, 2):
        tv = TurbovecIndex(doc_vecs.shape[1], bit_width=bits)
        tv.add(ids, doc_vecs)
        overlap, top1 = [], []
        for v in qvecs:
            truth = [i for i, _ in exact.search(v, 10)]
            got = [i for i, _ in tv.search(v, 10)]
            overlap.append(len(set(truth) & set(got)) / 10)
            top1.append(float(truth[0] == got[0]))
        result[f"{bits}bit"] = {
            "recall@10": statistics.mean(overlap),
            "top1_agreement": statistics.mean(top1),
            "bytes": len(tv.to_bytes()),
        }
    return result


def scaling(doc_vecs: np.ndarray, qvecs: np.ndarray) -> dict:
    """Search latency at larger sizes, built by adding noisy copies of the real vectors."""
    rng = np.random.default_rng(0)
    out = {}
    for n in (500, 10_000, 50_000):
        reps = -(-n // len(doc_vecs))
        big = np.tile(doc_vecs, (reps, 1))[:n]
        big = big + 0.05 * rng.standard_normal(big.shape).astype(np.float32)
        ids = np.arange(1, n + 1, dtype=np.uint64)
        row = {}
        for name, index in (("exact", ExactIndex(768)), ("turbovec4", TurbovecIndex(768, 4))):
            t = time.perf_counter()
            index.add(ids, big)
            build_s = time.perf_counter() - t
            lat = []
            for v in qvecs[:60]:
                t = time.perf_counter()
                index.search(v, 30)
                lat.append((time.perf_counter() - t) * 1000)
            row[name] = {
                "build_s": build_s,
                "p50_ms": statistics.median(lat),
                "p95_ms": pct(lat, 0.95),
            }
        out[str(n)] = row
    return out


def restart_timing(doc_vecs: np.ndarray, tmp: Path, n: int = 10_000) -> dict:
    """Rebuild vs load-from-blob at 10k memories (MEMORY.md section 7.3 expects rebuild < 1 s)."""
    rng = np.random.default_rng(1)
    vecs = np.tile(doc_vecs, (-(-n // len(doc_vecs)), 1))[:n]
    vecs = vecs + 0.05 * rng.standard_normal(vecs.shape).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    key = os.urandom(32)
    path = tmp / "big.db"
    with Vault.open(path, key) as vault:
        with vault.transaction() as con:
            for i in range(n):
                con.execute(
                    "INSERT INTO memories(id, content, memory_type, origin, confidence, source,"
                    " created_at, content_hash) VALUES (?, ?, 'semantic', 'observed', 0.85, 'bench',"
                    " '2026-10-25T00:00:00Z', ?)",
                    (i + 1, f"synthetic memory {i}", f"h{i}"),
                )
                con.execute(
                    "INSERT INTO embeddings VALUES (?, 'nomic-embed-text', 768, ?)",
                    (i + 1, vecs[i].astype("<f4").tobytes()),
                )
        store = MemoryStore(vault)
        t = time.perf_counter()
        how = store.start()
        rebuild_s = time.perf_counter() - t
        t = time.perf_counter()
        store.checkpoint_index()
        checkpoint_s = time.perf_counter() - t
        store2 = MemoryStore(vault)
        t = time.perf_counter()
        how2 = store2.start()
        load_s = time.perf_counter() - t
    return {
        "n": n,
        "first_start": {"how": how, "s": rebuild_s},
        "checkpoint_s": checkpoint_s,
        "second_start": {"how": how2, "s": load_s},
        "blob_mb": path.stat().st_size / 1e6,
    }


async def main() -> None:
    items, queries = build()
    t0 = time.perf_counter()
    doc_vecs, qvecs, embed_lat = await embed_all(items, queries)
    print(f"{len(items)} items, {len(queries)} queries embedded in {time.perf_counter() - t0:.1f}s")
    print(
        f"query embedding: p50 {statistics.median(embed_lat):.1f} ms, p95 {pct(embed_lat, 0.95):.1f} ms"
    )

    results: dict = {"date": datetime.now(UTC).isoformat(timespec="seconds")}
    results["query_embed_ms"] = {"p50": statistics.median(embed_lat), "p95": pct(embed_lat, 0.95)}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        vault, store, ids = build_store(items, doc_vecs, "turbovec", tmp, 0.0)

        print("\n== min_semantic sweep (hybrid + rerank, turbovec) ==")
        sweep = {}
        for thr in SWEEP:
            store.min_semantic = thr
            sweep[thr] = evaluate(store, ids, queries, qvecs)
            print(f"min_semantic {thr:.2f}: {fmt(sweep[thr])}")
        results["sweep"] = {str(k): v for k, v in sweep.items()}

        # choose the threshold that keeps quality and removes most answers to unanswerable queries
        def utility(r: dict) -> float:
            return r["gold_mrr"] - 0.5 * r["false_positive_rate"] - 2 * r["forbidden_rate"]

        best = max(SWEEP, key=lambda t: utility(sweep[t]))
        print(f"\nbest threshold by utility (MRR - 0.5*answers-when-none - 2*forbidden): {best}")
        store.min_semantic = best
        results["chosen_min_semantic"] = best

        print(f"\n== stages at min_semantic={best} ==")
        stages = {
            "fts only": {"mode": "fts", "rerank": False},
            "vector only": {"mode": "vector", "rerank": False},
            "hybrid (RRF)": {"mode": "hybrid", "rerank": False},
            "hybrid + rerank": {"mode": "hybrid", "rerank": True},
        }
        results["stages"] = {}
        for name, kw in stages.items():
            results["stages"][name] = evaluate(store, ids, queries, qvecs, **kw)
            print(f"{name:16s}: {fmt(results['stages'][name])}")
        print("\nper category, hybrid + rerank:")
        for cat, vals in results["stages"]["hybrid + rerank"].items():
            if isinstance(vals, dict) and cat != "latency_ms":
                print(f"  {cat:13s} " + " ".join(f"{k} {v:.2f}" for k, v in vals.items()))
        vault.close()

        vault2, store2, ids2 = build_store(items, doc_vecs, "exact", tmp, best)
        results["exact_index_pipeline"] = evaluate(store2, ids2, queries, qvecs)
        print(f"\nfull pipeline on exact index: {fmt(results['exact_index_pipeline'])}")
        vault2.close()

        results["index"] = index_comparison(items, doc_vecs, queries, qvecs)
        print("\n== Turbovec vs exact (recall@10 of the same real vectors) ==")
        for bits, r in results["index"].items():
            print(
                f"{bits}: recall@10 {r['recall@10']:.3f}, top-1 agreement {r['top1_agreement']:.3f}, {r['bytes'] / 1e6:.2f} MB"
            )

        results["scaling"] = scaling(doc_vecs, qvecs)
        print("\n== search latency by corpus size (k=30) ==")
        for n, row in results["scaling"].items():
            for name, r in row.items():
                print(
                    f"{n:>6} {name:10s} build {r['build_s']:.2f}s  p50 {r['p50_ms']:.2f} ms  p95 {r['p95_ms']:.2f} ms"
                )

        results["restart_10k"] = restart_timing(doc_vecs, tmp)
        print(f"\n== restart at 10k memories ==\n{json.dumps(results['restart_10k'], indent=2)}")

    total = (
        results["query_embed_ms"]["p95"] + results["stages"]["hybrid + rerank"]["latency_ms"]["p95"]
    )
    results["retrieval_p95_ms_estimate"] = total
    print(
        f"\nretrieval p95 estimate (embed p95 + search p95): {total:.0f} ms (NFR2.1 limit 800 ms)"
    )

    out = Path("bench/results") / f"retrieval_{datetime.now(UTC):%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
