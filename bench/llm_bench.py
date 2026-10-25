"""Chat model bench: load time, GPU share, first-token latency, decode speed, think:false.

Run: uv run python -m bench.llm_bench [model]
"""

import asyncio
import sys
import time

import httpx

from ragnarok.runtime.gateway import CHAT_MODEL, NUM_CTX, OLLAMA_URL, Gateway

SYSTEM = "You are Ragnarok, a concise personal assistant. Answer in at most two sentences."
FACTS = (
    "Known about the user: prefers meetings after 14:00; works on the payments-service repo; "
    "test_refund_rounding has been failing since 23 October; dentist appointment on 3 November. "
) * 12  # about 800 tokens of stable prefix, like the profile block + memory index
QUESTION = "Which of my meeting preferences should you apply when you schedule the retro?"


async def run_once(gw: Gateway, model: str, think: bool, *, stable_prefix: bool = True) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM + "\n" + FACTS},
        {"role": "user", "content": QUESTION if stable_prefix else QUESTION + f" ({time.time()})"},
    ]
    t0 = time.perf_counter()
    first = None
    text, thinking, last = "", "", {}
    async for chunk in gw.chat_stream(messages, model=model, think=think, seed=7, temperature=0):
        msg = chunk.get("message", {})
        if first is None and (msg.get("content") or msg.get("thinking")):
            first = time.perf_counter() - t0
        text += msg.get("content", "")
        thinking += msg.get("thinking", "")
        if chunk.get("done"):
            last = chunk
    return {
        "first_token_ms": (first or 0) * 1000,
        "total_ms": (time.perf_counter() - t0) * 1000,
        "load_ms": last.get("load_duration", 0) / 1e6,
        "prompt_tokens": last.get("prompt_eval_count", 0),
        "prompt_ms": last.get("prompt_eval_duration", 0) / 1e6,
        "out_tokens": last.get("eval_count", 0),
        "tok_s": last.get("eval_count", 0) / max(last.get("eval_duration", 1) / 1e9, 1e-9),
        "thinking_chars": len(thinking),
        "text": text.strip(),
    }


def gpu_share(model: str) -> str:
    for m in httpx.get(f"{OLLAMA_URL}/api/ps", timeout=10).json().get("models", []):
        if m["name"].startswith(model.split(":")[0]):
            pct = 100 * m["size_vram"] / m["size"] if m["size"] else 0
            gb = f"{m['size_vram'] / 1e9:.2f} of {m['size'] / 1e9:.2f} GB"
            return f"{pct:.0f}% GPU ({gb}), ctx {m.get('context_length')}"
    return "not loaded"


async def main(model: str) -> None:
    gw = Gateway(timeout=300)  # the first request includes the model load
    try:
        print(f"model {model}, num_ctx {NUM_CTX}")
        cold = await run_once(gw, model, think=False)
        print(
            f"cold (includes load): load {cold['load_ms']:.0f} ms, first token "
            f"{cold['first_token_ms']:.0f} ms, prompt {cold['prompt_tokens']} tok"
        )
        print("GPU:", gpu_share(model))
        runs = [await run_once(gw, model, think=False) for _ in range(5)]
        for name, key in (
            ("first token (cached prefix)", "first_token_ms"),
            ("decode tok/s", "tok_s"),
        ):
            vals = sorted(r[key] for r in runs)
            print(
                f"{name}: median {vals[len(vals) // 2]:.1f}, min {vals[0]:.1f}, max {vals[-1]:.1f}"
            )
        print(f"out tokens per answer: {[r['out_tokens'] for r in runs]}")
        fresh = await run_once(gw, model, think=False, stable_prefix=False)
        print(
            f"changed suffix: first token {fresh['first_token_ms']:.0f} ms, "
            f"prompt tokens evaluated {fresh['prompt_tokens']}"
        )
        thought = await run_once(gw, model, think=True)
        think_off, think_on = runs[0]["thinking_chars"], thought["thinking_chars"]
        print(f"think=False honored: {think_off == 0} (thinking chars {think_off})")
        print(f"think=True: {think_on} thinking chars, {thought['out_tokens']} tokens")
        print("sample answer:", runs[0]["text"])
    finally:
        await gw.aclose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else CHAT_MODEL))
