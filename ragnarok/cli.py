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

from ragnarok.chat import ChatApp
from ragnarok.memory.ingest import GatewayLlm, Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault, VaultKeyError
from ragnarok.persistent_memory import PersistentMemory
from ragnarok.runtime.gateway import CHAT_MODEL, EMBED_MODEL, OLLAMA_URL, Gateway
from ragnarok.ui import Console
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
    ui = Console()
    keystore = home / KEY_FILE
    if not keystore.exists():
        raise CliError("no keystore found. Run `init` first.")
    if (home / DPAPI_FILE).exists() and not use_recovery:
        try:
            return keys.unlock_with_dpapi(home / DPAPI_FILE)
        except keys.KeystoreError:
            ui.notice("Automatic unlock failed; asking for the passphrase.")
    what = "Recovery code" if use_recovery else "Passphrase"
    for attempt in range(MAX_UNLOCK_TRIES):
        try:
            secret = getpass.getpass(f" {what}: ")
            if use_recovery:
                return keys.unlock_with_recovery(keystore, secret)
            return keys.unlock_with_passphrase(keystore, secret)
        except keys.WrongSecret:
            left = MAX_UNLOCK_TRIES - attempt - 1
            if left:
                ui.error(f"Wrong {what.lower()}. {left} {'try' if left == 1 else 'tries'} left.")
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
    ui = Console()
    home = home_dir()
    if (home / KEY_FILE).exists():
        raise CliError(f"already initialised at {home}.")
    ui.write()
    ui.section("Set up Ragnarok")
    ui.notice("Your memories are encrypted with a key that only your passphrase can open.")
    ui.write()
    first = getpass.getpass(" Choose a passphrase (8+ characters): ")
    if first != getpass.getpass(" Repeat it: "):
        raise CliError("the passphrases do not match.")
    try:
        created = keys.create_keystore(home / KEY_FILE, first)
    except keys.KeystoreError as exc:
        raise CliError(str(exc)) from exc
    Vault.open(home / VAULT_FILE, created.vault_key).close()
    if args.dpapi:
        keys.enable_dpapi(home / DPAPI_FILE, created.vault_key)
    ui.write()
    ui.success(f"Created an encrypted vault in {home}")
    ui.write()
    ui.box("Recovery code", ["", ui.amber(created.recovery_code), ""], tint=179)
    ui.write()
    ui.write(" " + ui.bold("Save this code somewhere safe."))
    ui.notice("It is shown once, and it is the only way back in if you forget the passphrase.")
    if args.dpapi:
        ui.write()
        ui.notice("Auto-unlock is on: anyone signed in to this Windows account can open the vault.")
    ui.write()
    ui.write(f" Next: {ui.sky('uv run python -m ragnarok chat')}")
    ui.write()
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    home = home_dir()
    rt = open_runtime(home, unlock(home, use_recovery=args.recovery))
    ui = Console()
    try:
        pm = PersistentMemory(rt.vault, rt.store, rt.svc, rt.ingestor)
        asyncio.run(ChatApp(rt, ui, folder=str(home), maintenance=pm.maintain).run())
    except KeyboardInterrupt:
        ui.write()
        ui.notice("Interrupted.")
    finally:
        rt.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    home = home_dir()
    rt = open_runtime(home, unlock(home))
    ui = Console()
    try:
        report = asyncio.run(rt.ingestor.ingest_pending(limit=args.limit))
    finally:
        rt.close()
    ui.success(
        f"{report.events} events read: {report.added} new, {report.superseded} changed, "
        f"{report.noop + report.updated} confirmed."
    )
    if report.deferred:
        ui.error(f"{report.deferred} events could not be read (is the model running?).")
    return 0


def _show_memory(ui: Console, m) -> None:
    flags = ("pinned, " if m.pinned else "") + m.status
    ui.write(f"   {ui.amber(f'm{m.id}')}  {m.content}")
    ui.write(
        "      "
        + ui.faint(f"{m.memory_type}, {m.origin} {m.confidence:.2f}, {m.created_at[:10]}, {flags}")
    )


CONTROL_ACTIONS = (
    "pin",
    "unpin",
    "correct",
    "revert",
    "export",
    "stats",
    "consolidate",
    "prune",
    "maintain",
    "undo-summary",
)


def _need_id(act: str, arg: str) -> int:
    if not arg.isdigit():
        raise CliError(f"`memory {act}` needs a memory number.")
    return int(arg)


async def _controls(pm: PersistentMemory, ui: Console, args: argparse.Namespace) -> None:
    """Pin, correct, revert, export, stats and the tidy-up commands."""
    act, arg = args.action, args.arg
    if act in ("pin", "unpin", "revert", "undo-summary"):
        mid = _need_id(act, arg)
        if act == "pin":
            await pm.pin(mid)
            ui.success(f"Pinned m{mid}. It is always in my prompt and never archived.")
        elif act == "unpin":
            await pm.unpin(mid)
            ui.success(f"Unpinned m{mid}.")
        elif act == "revert":
            newer = await pm.revert(mid)
            extra = f" m{newer} was archived (not deleted)." if newer else ""
            ui.success(f"Brought back m{mid}.{extra}")
        else:
            restored = await pm.undo_summary(mid)
            ui.success(f"Removed summary m{mid}; restored {len(restored)} hidden memories.")
    elif act == "correct":
        mid = _need_id(act, arg)
        if not args.text:
            raise CliError('`memory correct N --text "the right fact"` needs the new text.')
        new_id = await pm.correct(mid, args.text)
        ui.success(
            f"Replaced m{mid} with m{new_id}. The old version stays in `memory history {new_id}`."
        )
    elif act == "export":
        if not arg:
            raise CliError("`memory export PATH` needs a file to write.")
        try:
            count = await pm.export(arg, allow_plaintext=args.yes_plaintext)
        except PermissionError as exc:
            raise CliError(f"{exc}. Add --yes-plaintext if that is what you want.") from exc
        ui.success(f"Wrote {count} memories (all versions) to {arg}.")
        ui.notice("That file is readable text, not encrypted. Keep it somewhere private.")
    elif act == "stats":
        s = await pm.stats()
        ui.write()
        ui.section("Memory")
        ui.write(
            f"   {ui.amber(str(s['active']))} active {ui.dim('·')} {s['pinned']} pinned"
            f" {ui.dim('·')} {s['superseded']} replaced {ui.dim('·')} {s['archived']} archived"
        )
        types = ", ".join(f"{n} {t}" for t, n in sorted(s["by_type"].items())) or "none"
        ui.write(f"   {ui.faint('by type  ')} {types}")
        pending = s["events_not_yet_learned"]
        ui.write(f"   {ui.faint('events   ')} {s['events']} recorded, {pending} not yet learned")
        ui.write(f"   {ui.faint('index    ')} {s['indexed_vectors']} vectors")
        ui.write()
    elif act == "consolidate":
        r = await pm.consolidate()
        ui.success(f"{r.summarized} of {r.groups} weekly groups summarised.")
        if r.rejected:
            ui.notice("Rejected: " + ", ".join(f"{n} {why}" for why, n in r.rejected.items()))
        if r.errors:
            ui.error(f"{r.errors} groups failed (is the model running?); they will be retried.")
    elif act == "prune":
        r = await pm.prune(dry_run=args.dry_run)
        verb = "Would archive" if args.dry_run else "Archived"
        ui.success(
            f"{verb} {len(r.archived)} of {r.examined} summarised memories ({r.kept} still useful)."
        )
    else:  # maintain
        r = await pm.maintain(force=True)
        ui.success(
            f"{r.consolidation.summarized} summaries, {len(r.pruned.archived)} archived, "
            f"{r.events_expired} old events removed."
        )


def cmd_memory(args: argparse.Namespace) -> int:
    home = home_dir()
    rt = open_runtime(home, unlock(home))
    ui = Console()
    try:
        store, act = rt.store, args.action
        if act in CONTROL_ACTIONS:
            pm = PersistentMemory(rt.vault, rt.store, rt.svc, rt.ingestor)
            try:
                asyncio.run(_controls(pm, ui, args))
            except ValueError as exc:
                raise CliError(str(exc)) from exc
            return 0
        if act == "list":
            ui.memories(
                store.list_memories(status=args.status, memory_type=args.type, limit=args.limit)
            )
        elif act == "search":
            ui.memories([h.memory for h in asyncio.run(rt.svc.recall(args.arg, k=8))])
        elif act in ("show", "history", "forget", "archive", "restore"):
            if not args.arg.isdigit():
                raise CliError(f"`memory {act}` needs a memory number.")
            mid = int(args.arg)
            if act == "show":
                m = store.get(mid)
                if m is None:
                    raise CliError("no such memory.")
                ui.write()
                _show_memory(ui, m)
                sources = rt.vault.query(
                    "SELECT e.ts, e.kind FROM memory_sources s JOIN events e ON e.id = s.event_id"
                    " WHERE s.memory_id = ?",
                    (mid,),
                )
                for ts, kind in sources:
                    ui.write("      " + ui.faint(f"from {kind} at {ts}"))
                ui.write()
            elif act == "history":
                ui.write()
                for m in store.history(mid):
                    _show_memory(ui, m)
                ui.write()
            elif act == "forget":
                if store.delete(mid):
                    ui.success(f"Deleted m{mid}.")
                else:
                    ui.error("No such memory.")
            else:
                try:
                    getattr(store, act)(mid)
                except ValueError as exc:
                    raise CliError(str(exc)) from exc
                ui.success(f"{'Archived' if act == 'archive' else 'Restored'} m{mid}.")
        else:
            raise CliError(f"unknown memory action {act!r}.")
    finally:
        rt.close()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    ui = Console()
    if args.command:
        from ragnarok.gui import startup

        if args.command == "on":
            startup.install_command()
            note = "" if startup.on_path() else " (add that folder to your PATH first)"
            ui.success(f"Type meow anywhere to call the cat{note}.")
        else:
            ui.success(f"Removed {startup.remove_command()} files.")
        return 0
    if args.startup:
        from ragnarok.gui import startup

        if args.startup == "on":
            ui.success(f"The cat will appear when you log in ({startup.install()}).")
        else:
            ui.success("Removed." if startup.remove() else "It was not set to start with Windows.")
        return 0
    from ragnarok.gui.app import main as run_gui

    return run_gui()


def cmd_doctor(args: argparse.Namespace) -> int:
    ui = Console()
    failures = 0

    def line(ok: bool | None, what: str, detail: str = "") -> None:
        nonlocal failures
        if ok:
            mark = ui.good(ui.g["ok"])
        elif ok is None:
            mark = ui.amber("!")
        else:
            mark = ui.bad(ui.g["no"])
            failures += 1
        ui.write(f"   {mark} {what}" + (f"  {ui.faint(detail)}" if detail else ""))

    ui.write()
    ui.section("Checking your setup")
    ui.write()
    base = os.environ.get("RAGNAROK_OLLAMA_URL", OLLAMA_URL)
    try:
        version = httpx.get(f"{base}/api/version", timeout=3).json()["version"]
        line(True, "Ollama is running on this machine only", f"version {version}")
    except Exception:
        line(False, "Ollama is not reachable", f"nothing at {base}; run scripts/start_ollama.ps1")
        ui.write()
        return 1
    line(
        os.environ.get("OLLAMA_NO_CLOUD") == "1" or None,
        "Cloud features are switched off (OLLAMA_NO_CLOUD=1)",
    )
    log = Path(os.environ.get("LOCALAPPDATA", ".")) / "Ragnarok" / "logs" / "ollama-serve.err.log"
    try:
        text = log.read_text(encoding="utf-8", errors="ignore")
        line("cloud disabled: true" in text or None, "The server log confirms cloud is disabled")
    except OSError:
        line(None, "No server log found", "start Ollama with scripts/start_ollama.ps1")

    have = {m["name"] for m in httpx.get(f"{base}/api/tags", timeout=5).json()["models"]}
    for model in (CHAT_MODEL, f"{EMBED_MODEL}:latest"):
        line(model in have, f"Model {model} is installed")
    loaded = httpx.get(f"{base}/api/ps", timeout=5).json()["models"]
    if not loaded:
        line(None, "No model loaded yet", "the first chat loads them")
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
        line(False, "Data folder", str(exc))
        ui.write()
        return 1
    if (home / KEY_FILE).exists():
        line(True, "Keystore found", str(home / KEY_FILE))
    else:
        line(None, "No keystore yet", "run `init` to create your vault")
    vault_path = home / VAULT_FILE
    if vault_path.exists():
        header = vault_path.read_bytes()[:16]
        line(not header.startswith(b"SQLite format 3"), "The vault file is encrypted")
    else:
        line(None, "No vault yet", "run `init`")
    ui.write()
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
        "action",
        choices=[
            "list",
            "show",
            "search",
            "history",
            "forget",
            "archive",
            "restore",
            *CONTROL_ACTIONS,
        ],
    )
    mem.add_argument("arg", nargs="?", default="", help="memory number, search text, or file path")
    mem.add_argument("--status", default="active", choices=["active", "archived", "superseded"])
    mem.add_argument(
        "--type", default=None, help="only this kind: preference, semantic, summary..."
    )
    mem.add_argument("--limit", type=int, default=50)
    mem.add_argument("--text", default="", help="with `correct`: the right version of the fact")
    mem.add_argument(
        "--yes-plaintext", action="store_true", help="with `export`: I accept an unencrypted file"
    )
    mem.add_argument("--dry-run", action="store_true", help="with `prune`: show, don't archive")
    mem.set_defaults(run=cmd_memory)

    doctor = sub.add_parser("doctor", help="check the setup")
    doctor.set_defaults(run=cmd_doctor)

    gui = sub.add_parser("gui", help="the pixel cat on your taskbar (click it to chat)")
    gui.add_argument(
        "--startup", choices=["on", "off"], help="start the cat when you log in to Windows (or not)"
    )
    gui.add_argument(
        "--command",
        choices=["on", "off"],
        help="add (or remove) the short command: meow",
    )
    gui.set_defaults(run=cmd_gui)
    return p


def main(argv: list[str] | None = None, *, _run: Callable | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return (_run or args.run)(args)
    except CliError as exc:
        Console(sys.stderr).error(str(exc))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
