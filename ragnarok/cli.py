"""`rag`: terminal front end. Run as `uv run python -m ragnarok <command>`.

  init                     create the keystore and vault (shows the recovery code once)
  chat                     talk to the agent; it remembers across sessions
  memory list|show|search|history|forget|archive|restore
  ingest                   turn pending events into memories now
  doctor                   check local-only mode, models, GPU, and encryption

The models run in Ollama (a separate background process on this machine); this program only
talks to it on 127.0.0.1. Runtime data lives in %LOCALAPPDATA%\\Ragnarok (override: RAGNAROK_HOME),
never in a OneDrive folder.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ragnarok.memory.ingest import GatewayLlm, Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault, VaultKeyError
from ragnarok.policy.permissions import Decision
from ragnarok.runtime.gateway import CHAT_MODEL, EMBED_MODEL, OLLAMA_URL, Gateway
from ragnarok.runtime.loop import Agent, Session
from ragnarok.runtime.tools import memory_tools
from ragnarok.skills.apps import app_tools
from ragnarok.vault import keys

KEY_FILE, DPAPI_FILE, VAULT_FILE = "key.json", "key.dpapi", "vault.db"
MAX_UNLOCK_TRIES = 3


class CliError(Exception):
    """A problem to show the user as one line (no traceback)."""


def home_dir() -> Path:
    path = Path(os.environ.get("RAGNAROK_HOME") or keys.runtime_dir())
    if "onedrive" in str(path.resolve()).lower():
        raise CliError(
            f"{path} is inside OneDrive, which would sync your vault. "
            "Set RAGNAROK_HOME to a folder outside OneDrive."
        )
    return path


@dataclass
class Runtime:
    vault: Vault
    store: MemoryStore
    svc: MemoryService
    ingestor: Ingestor
    gateway: Gateway

    def close(self) -> None:
        self.vault.close()


# ---- unlocking -----------------------------------------------------------------------------


def unlock(home: Path, *, use_recovery: bool = False) -> bytes:
    """DPAPI copy if enabled, otherwise the passphrase (or recovery code)."""
    keystore = home / KEY_FILE
    if not keystore.exists():
        raise CliError("no keystore found. Run `init` first.")
    if (home / DPAPI_FILE).exists() and not use_recovery:
        try:
            return keys.unlock_with_dpapi(home / DPAPI_FILE)
        except keys.KeystoreError:
            print("DPAPI unlock failed; asking for the passphrase.", file=sys.stderr)
    for attempt in range(MAX_UNLOCK_TRIES):
        try:
            if use_recovery:
                return keys.unlock_with_recovery(keystore, getpass.getpass("Recovery code: "))
            return keys.unlock_with_passphrase(keystore, getpass.getpass("Passphrase: "))
        except keys.WrongSecret:
            left = MAX_UNLOCK_TRIES - attempt - 1
            if left:
                what = "recovery code" if use_recovery else "passphrase"
                print(f"Wrong {what}. {left} tries left.")
    raise CliError("wrong passphrase or recovery code.")


def open_runtime(
    home: Path, key: bytes, gateway: Gateway | None = None, *, llm_num_predict: int = 512
) -> Runtime:
    try:
        vault = Vault.open(home / VAULT_FILE, key)
    except VaultKeyError as exc:
        raise CliError("the key does not open this vault.") from exc
    gw = gateway or Gateway(timeout=300)
    store = MemoryStore(vault)
    store.start()
    svc = MemoryService(store, gw)
    ingestor = Ingestor(store, gw, GatewayLlm(gw, num_predict=llm_num_predict))
    return Runtime(vault, store, svc, ingestor, gw)


# ---- commands ------------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    home = home_dir()
    if (home / KEY_FILE).exists():
        raise CliError(f"already initialised at {home}.")
    first = getpass.getpass("Choose a passphrase (8+ characters): ")
    if first != getpass.getpass("Repeat it: "):
        raise CliError("the passphrases do not match.")
    try:
        created = keys.create_keystore(home / KEY_FILE, first)
    except keys.KeystoreError as exc:
        raise CliError(str(exc)) from exc
    Vault.open(home / VAULT_FILE, created.vault_key).close()
    if args.dpapi:
        keys.enable_dpapi(home / DPAPI_FILE, created.vault_key)
    print(f"Created an encrypted vault in {home}")
    print("\nRECOVERY CODE (shown once; store it somewhere safe, it is the only way back if you")
    print(f"forget the passphrase):\n\n    {created.recovery_code}\n")
    if args.dpapi:
        print("Auto-unlock is ON: anyone logged in to this Windows account can open the vault.")
    return 0


async def _approve(name: str, args: dict, decision: Decision) -> bool:
    answer = await asyncio.to_thread(input, f"  allow {name}({args}) [{decision.rule}]? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def _chat(rt: Runtime) -> None:
    tools = memory_tools(rt.svc, rt.ingestor) + app_tools()
    agent = Agent(rt.store, rt.svc, rt.ingestor, rt.gateway, approve=_approve, tools=tools)
    session = Session()
    print("Loading models...", end=" ", flush=True)
    print(f"ready in {await rt.gateway.warm_up():.1f}s. Type /help for commands.\n")
    while True:
        try:
            line = (await asyncio.to_thread(input, "you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            if await _slash(line, rt, agent):
                break
            continue

        async def show(text: str) -> None:
            print(text, end="", flush=True)

        print("rag> ", end="", flush=True)
        result = await agent.turn(session, line, on_delta=show)
        if result.stopped == "model-error":
            print(result.text, end="")
        print()
        for t in result.trace:
            mark = "ok" if t.allowed else "blocked"
            print(f"     [{t.tool} {mark}] {t.result.splitlines()[0] if t.result else ''}")
    print("Saving what I learned...", end=" ", flush=True)
    report = await rt.ingestor.ingest_pending()
    print(
        f"{report.added} new, {report.superseded} changed, "
        f"{report.noop + report.updated} confirmed."
    )


async def _slash(line: str, rt: Runtime, agent: Agent) -> bool:
    cmd, _, arg = line.partition(" ")
    if cmd in ("/quit", "/exit"):
        return True
    if cmd == "/help":
        print("/memories  list what I remember   /profile  what is always in my prompt")
        print("/forget N  delete memory N         /ingest   learn from this session now")
        print("/quit      save and exit")
    elif cmd == "/memories":
        for m in rt.store.list_memories(limit=30):
            print(f"  [m:{m.id}] {m.content}")
    elif cmd == "/profile":
        print(agent.profile.get()[0])
    elif cmd == "/forget" and arg.strip().isdigit():
        print("deleted." if rt.store.delete(int(arg)) else "no such memory.")
    elif cmd == "/ingest":
        print(await rt.ingestor.ingest_pending())
    else:
        print("unknown command; try /help")
    return False


def cmd_chat(args: argparse.Namespace) -> int:
    home = home_dir()
    rt = open_runtime(home, unlock(home, use_recovery=args.recovery))
    try:
        asyncio.run(_chat(rt))
    finally:
        rt.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    home = home_dir()
    rt = open_runtime(home, unlock(home))
    try:
        print(asyncio.run(rt.ingestor.ingest_pending(limit=args.limit)))
    finally:
        rt.close()
    return 0


def _print_memory(m) -> None:
    flags = ("pinned " if m.pinned else "") + m.status
    print(f"[m:{m.id}] {m.content}")
    print(f"        {m.memory_type}, {m.origin} {m.confidence:.2f}, {m.created_at[:10]}, {flags}")


def cmd_memory(args: argparse.Namespace) -> int:
    home = home_dir()
    rt = open_runtime(home, unlock(home))
    try:
        store, act = rt.store, args.action
        if act == "list":
            for m in store.list_memories(status=args.status, limit=args.limit):
                _print_memory(m)
        elif act == "search":
            for h in asyncio.run(rt.svc.recall(args.arg, k=8)):
                _print_memory(h.memory)
        elif act in ("show", "history", "forget", "archive", "restore"):
            if not args.arg.isdigit():
                raise CliError(f"`memory {act}` needs a memory number.")
            mid = int(args.arg)
            if act == "show":
                m = store.get(mid)
                if m is None:
                    raise CliError("no such memory.")
                _print_memory(m)
                sources = rt.vault.query(
                    "SELECT e.ts, e.kind FROM memory_sources s JOIN events e ON e.id = s.event_id"
                    " WHERE s.memory_id = ?",
                    (mid,),
                )
                for ts, kind in sources:
                    print(f"        from {kind} at {ts}")
            elif act == "history":
                for m in store.history(mid):
                    _print_memory(m)
            elif act == "forget":
                print("deleted." if store.delete(mid) else "no such memory.")
            else:
                try:
                    getattr(store, act)(mid)
                except ValueError as exc:
                    raise CliError(str(exc)) from exc
                print(f"{act}d.".replace("ee", "e"))
        else:
            raise CliError(f"unknown memory action {act!r}.")
    finally:
        rt.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    failures = 0

    def line(ok: bool | None, what: str, detail: str = "") -> None:
        nonlocal failures
        tag = "ok  " if ok else ("warn" if ok is None else "FAIL")
        failures += ok is False
        print(f"[{tag}] {what}" + (f": {detail}" if detail else ""))

    base = os.environ.get("RAGNAROK_OLLAMA_URL", OLLAMA_URL)
    try:
        version = httpx.get(f"{base}/api/version", timeout=3).json()["version"]
        line(True, "Ollama reachable on loopback", f"version {version}")
    except Exception:
        line(
            False,
            "Ollama reachable on loopback",
            f"nothing at {base}; run scripts/start_ollama.ps1",
        )
        return 1
    line(os.environ.get("OLLAMA_NO_CLOUD") == "1" or None, "OLLAMA_NO_CLOUD=1 in this environment")
    log = Path(os.environ.get("LOCALAPPDATA", ".")) / "Ragnarok" / "logs" / "ollama-serve.err.log"
    try:
        text = log.read_text(encoding="utf-8", errors="ignore")
        line("cloud disabled: true" in text or None, "server log says cloud is disabled", str(log))
    except OSError:
        line(None, "server log not found", "start Ollama with scripts/start_ollama.ps1")

    have = {m["name"] for m in httpx.get(f"{base}/api/tags", timeout=5).json()["models"]}
    for model in (CHAT_MODEL, f"{EMBED_MODEL}:latest"):
        line(model in have, f"model {model} installed")
    loaded = httpx.get(f"{base}/api/ps", timeout=5).json()["models"]
    if not loaded:
        line(None, "no model loaded yet", "the first chat loads them")
    for m in loaded:
        gpu = 100 * m["size_vram"] / m["size"] if m["size"] else 0
        line(
            gpu >= 99.9,
            f"{m['name']} runs on the GPU",
            f"{gpu:.0f}% GPU, context {m['context_length']}",
        )

    try:
        home = home_dir()
    except CliError as exc:
        line(False, "data folder", str(exc))
        return 1
    if (home / KEY_FILE).exists():
        line(True, "keystore present", str(home / KEY_FILE))
    else:
        line(None, "no keystore yet", "run `init` to create your vault")
    vault_path = home / VAULT_FILE
    if vault_path.exists():
        header = vault_path.read_bytes()[:16]
        line(
            not header.startswith(b"SQLite format 3"), "vault file is encrypted (not plain SQLite)"
        )
    else:
        line(None, "no vault yet", "run `init`")
    return 1 if failures else 0


# ---- entry point ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rag", description="Ragnarok: a local agent that remembers you."
    )
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the encrypted vault")
    init.add_argument("--dpapi", action="store_true", help="also allow unlock without a passphrase")
    init.set_defaults(run=cmd_init)

    chat = sub.add_parser("chat", help="talk to the agent")
    chat.add_argument("--recovery", action="store_true", help="unlock with the recovery code")
    chat.set_defaults(run=cmd_chat)

    ingest = sub.add_parser("ingest", help="learn from pending events now")
    ingest.add_argument("--limit", type=int, default=40)
    ingest.set_defaults(run=cmd_ingest)

    mem = sub.add_parser("memory", help="inspect and edit memories")
    mem.add_argument(
        "action", choices=["list", "show", "search", "history", "forget", "archive", "restore"]
    )
    mem.add_argument("arg", nargs="?", default="", help="memory number, or the search text")
    mem.add_argument("--status", default="active", choices=["active", "archived", "superseded"])
    mem.add_argument("--limit", type=int, default=50)
    mem.set_defaults(run=cmd_memory)

    doctor = sub.add_parser("doctor", help="check the setup")
    doctor.set_defaults(run=cmd_doctor)
    return p


def main(argv: list[str] | None = None, *, _run: Callable | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return (_run or args.run)(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
