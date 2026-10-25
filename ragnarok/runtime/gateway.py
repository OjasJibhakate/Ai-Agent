"""The only path to a model: Ollama on loopback (AGENTS.md hard rule 1, ARCHITECTURE section 2.3).

Load options never vary per request, because a changed option makes Ollama reload the model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import numpy as np

OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "qwen3.5:4b"
NUM_CTX = 16384  # identical on every request; a different value makes Ollama reload the model
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass
class ChatResult:
    """A finished chat turn: text, requested tool calls, and Ollama's timing counters."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    first_token_ms: float | None = None
    eval_count: int = 0
    prompt_eval_count: int = 0


class GatewayError(Exception):
    """A request was refused by policy or Ollama failed."""


def check_policy(base_url: str, model: str) -> None:
    host = urlparse(base_url).hostname
    if host not in LOOPBACK_HOSTS:
        raise GatewayError(f"refusing non-loopback host: {host}")
    if model.endswith("-cloud") or ":cloud" in model:
        raise GatewayError(f"refusing cloud model: {model}")


class Gateway:
    def __init__(self, base_url: str = OLLAMA_URL, *, timeout: float = 60.0) -> None:
        self.base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def embed(self, texts: list[str], model: str = EMBED_MODEL) -> np.ndarray:
        """Embed texts into a float32 matrix (n, dim). Callers add the search_* prefixes."""
        check_policy(self.base_url, model)
        try:
            resp = await self._client.post("/api/embed", json={"model": model, "input": texts})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError(f"embed failed: {type(exc).__name__}") from exc
        return np.asarray(resp.json()["embeddings"], dtype=np.float32)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = CHAT_MODEL,
        think: bool = False,
        num_predict: int = 1024,
        temperature: float = 0.3,
        seed: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield Ollama's streamed chat chunks; the last one (`done`) carries timing counters."""
        check_policy(self.base_url, model)
        options: dict[str, Any] = {
            "num_ctx": NUM_CTX,
            "num_predict": num_predict,
            "temperature": temperature,
        }
        if seed is not None:
            options["seed"] = seed
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "think": think,
            "keep_alive": -1,
            "options": options,
        }
        if tools:
            body["tools"] = tools
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        yield json.loads(line)
        except httpx.HTTPError as exc:
            raise GatewayError(f"chat failed: {type(exc).__name__}") from exc

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        num_predict: int = 1024,
        temperature: float = 0.3,
    ) -> ChatResult:
        """Stream a chat turn, forwarding text to `on_delta`, and return the assembled result."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        result = ChatResult()
        async for chunk in self.chat_stream(
            messages, tools=tools, num_predict=num_predict, temperature=temperature
        ):
            msg = chunk.get("message", {})
            if text := msg.get("content"):
                if result.first_token_ms is None:
                    result.first_token_ms = (loop.time() - start) * 1000
                result.content += text
                if on_delta:
                    await on_delta(text)
            result.tool_calls.extend(msg.get("tool_calls") or [])
            if chunk.get("done"):
                result.eval_count = chunk.get("eval_count", 0)
                result.prompt_eval_count = chunk.get("prompt_eval_count", 0)
        return result

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        model: str = CHAT_MODEL,
        num_predict: int = 512,
        seed: int = 7,
    ) -> dict[str, Any]:
        """One non-streaming call whose output must match `schema` (temperature 0, fixed seed)."""
        check_policy(self.base_url, model)
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": schema,
            "keep_alive": -1,
            "options": {
                "num_ctx": NUM_CTX,
                "num_predict": num_predict,
                "temperature": 0,
                "seed": seed,
            },
        }
        try:
            resp = await self._client.post("/api/chat", json=body)
            resp.raise_for_status()
            return json.loads(resp.json()["message"]["content"])
        except httpx.HTTPError as exc:
            raise GatewayError(f"chat_json failed: {type(exc).__name__}") from exc
        except (ValueError, KeyError) as exc:
            raise GatewayError("chat_json returned invalid JSON") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
