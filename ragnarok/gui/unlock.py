"""Unlocking the vault (and creating it the first time) without a terminal.

If the owner chose automatic unlock when the vault was made (DPAPI), nothing is asked. Otherwise a
small dialog asks for the passphrase. The same rules as the command line apply: three tries, and
the recovery code is shown once, at creation.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ragnarok.gui import theme as t
from ragnarok.memory.vault import Vault
from ragnarok.vault import keys

KEY_FILE, DPAPI_FILE, VAULT_FILE = "key.json", "key.dpapi", "vault.db"
MIN_LENGTH = 8


@dataclass
class Answer:
    passphrase: str
    automatic: bool = False


def remember_on_this_account(home: Path, vault_key: bytes) -> bool:
    """Seal the vault key to this Windows account (DPAPI) so the next start asks nothing.
    False if Windows would not do it; the passphrase keeps working either way."""
    try:
        keys.enable_dpapi(home / DPAPI_FILE, vault_key)
    except (keys.KeystoreError, OSError):
        return False
    return True


def forget_this_account(home: Path) -> None:
    """Back to asking for the passphrase at every start."""
    keys.disable_dpapi(home / DPAPI_FILE)


def remembered(home: Path) -> bool:
    return (home / DPAPI_FILE).exists()


def _dialog(
    root: tk.Tk, title: str, heading: str, *, repeat: bool, automatic: bool, error: str = ""
):
    """A small dark window with one or two hidden fields. Returns an Answer, or None if closed."""
    result: list[Answer | None] = [None]
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=t.BG)
    win.attributes("-topmost", True)
    win.resizable(False, False)
    tk.Label(win, text="✦  Ragnarok", fg=t.VIOLET, bg=t.BG, font=t.TITLE).pack(
        anchor="w", padx=22, pady=(18, 2)
    )
    tk.Label(win, text=heading, fg=t.DIM, bg=t.BG, font=t.UI, wraplength=320, justify="left").pack(
        anchor="w", padx=22
    )

    def field(label: str) -> tk.Entry:
        tk.Label(win, text=label, fg=t.TEXT, bg=t.BG, font=t.SMALL).pack(
            anchor="w", padx=22, pady=(10, 0)
        )
        entry = tk.Entry(
            win, show="•", width=34, bg=t.PANEL, fg=t.TEXT, insertbackground=t.CYAN, relief="flat",
            font=t.UI, highlightthickness=1, highlightbackground=t.BORDER, highlightcolor=t.VIOLET,
        )  # fmt: skip
        entry.pack(padx=22, ipady=5)
        return entry

    first = field("Passphrase")
    second = field("Repeat it") if repeat else None
    keep = tk.BooleanVar(value=False)
    if automatic:
        tk.Checkbutton(
            win, text="Unlock automatically on this Windows account", variable=keep, fg=t.DIM,
            bg=t.BG, selectcolor=t.PANEL, activebackground=t.BG, activeforeground=t.TEXT, font=t.SMALL,
        ).pack(anchor="w", padx=18, pady=(8, 0))  # fmt: skip
        tk.Label(
            win, text="Then anyone signed in to Windows as you can open it without the passphrase.",
            fg=t.DIM, bg=t.BG, font=t.SMALL, wraplength=320, justify="left",
        ).pack(anchor="w", padx=40)  # fmt: skip
    message = tk.Label(
        win, text=error, fg=t.CORAL, bg=t.BG, font=t.SMALL, wraplength=320, justify="left"
    )
    message.pack(anchor="w", padx=22, pady=(6, 0))

    def submit(_event: object = None) -> None:
        text = first.get()
        if repeat:
            if len(text) < MIN_LENGTH:
                message.config(text=f"Use at least {MIN_LENGTH} characters.")
                return
            if second is not None and second.get() != text:
                message.config(text="The two passphrases do not match.")
                return
        elif not text:
            return
        result[0] = Answer(text, keep.get())
        win.destroy()

    row = tk.Frame(win, bg=t.BG)
    row.pack(fill="x", padx=22, pady=(10, 18))
    ok = tk.Label(
        row, text="  Continue  ", bg=t.VIOLET, fg=t.BG, font=t.UI_BOLD, cursor="hand2", pady=4
    )
    ok.pack(side="left")
    ok.bind("<Button-1>", submit)
    cancel = tk.Label(
        row, text="  Quit  ", bg=t.BORDER, fg=t.TEXT, font=t.UI, cursor="hand2", pady=4
    )
    cancel.pack(side="left", padx=8)
    cancel.bind("<Button-1>", lambda _e: win.destroy())
    win.bind("<Return>", submit)
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry(f"+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 3}")
    win.after(80, lambda: (win.focus_force(), first.focus_set()))
    win.grab_set()
    root.wait_window(win)
    return result[0]


def _show_recovery(root: tk.Tk, code: str) -> None:
    win = tk.Toplevel(root)
    win.title("Your recovery code")
    win.configure(bg=t.BG)
    win.attributes("-topmost", True)
    tk.Label(win, text="Save this recovery code", fg=t.AMBER, bg=t.BG, font=t.TITLE).pack(
        anchor="w", padx=22, pady=(18, 4)
    )
    tk.Label(
        win, fg=t.DIM, bg=t.BG, font=t.UI, justify="left", wraplength=340,
        text="It is shown once. It is the only way back in if you forget your passphrase.",
    ).pack(anchor="w", padx=22)  # fmt: skip
    box = tk.Label(win, text=code, fg=t.AMBER, bg=t.PANEL, font=("Consolas", 14), padx=14, pady=10)
    box.pack(padx=22, pady=12)

    def copy(_e: object = None) -> None:
        win.clipboard_clear()
        win.clipboard_append(code)
        copy_button.config(text="  Copied ✓  ")

    row = tk.Frame(win, bg=t.BG)
    row.pack(padx=22, pady=(0, 18), anchor="w")
    copy_button = tk.Label(
        row, text="  Copy  ", bg=t.BORDER, fg=t.TEXT, font=t.UI, cursor="hand2", pady=4
    )
    copy_button.pack(side="left")
    copy_button.bind("<Button-1>", copy)
    done = tk.Label(
        row, text="  I saved it  ", bg=t.VIOLET, fg=t.BG, font=t.UI_BOLD, cursor="hand2", pady=4
    )
    done.pack(side="left", padx=8)
    done.bind("<Button-1>", lambda _e: win.destroy())
    win.update_idletasks()
    win.geometry(
        f"+{(win.winfo_screenwidth() - win.winfo_reqwidth()) // 2}+{win.winfo_screenheight() // 3}"
    )
    win.grab_set()
    root.wait_window(win)


def first_run(root: tk.Tk, home: Path, ask: Callable[..., Answer | None] = _dialog) -> bytes | None:
    answer = ask(
        root, "Set up Ragnarok", "Choose a passphrase. It encrypts everything I remember about you, "
        "and only you hold it.", repeat=True, automatic=True,
    )  # fmt: skip
    if answer is None:
        return None
    home.mkdir(parents=True, exist_ok=True)
    created = keys.create_keystore(home / KEY_FILE, answer.passphrase)
    Vault.open(home / VAULT_FILE, created.vault_key).close()
    if answer.automatic:
        remember_on_this_account(home, created.vault_key)
    _show_recovery(root, created.recovery_code)
    return created.vault_key


def unlock(root: tk.Tk, home: Path, ask: Callable[..., Answer | None] = _dialog) -> bytes | None:
    """The vault key, or None if the owner closed the dialog or ran out of tries.

    `ask` shows the passphrase dialog (it is a parameter so tests can answer for the owner)."""
    keystore = home / KEY_FILE
    if not keystore.exists():
        return first_run(root, home, ask)
    if remembered(home):
        try:
            return keys.unlock_with_dpapi(home / DPAPI_FILE)
        except keys.KeystoreError:
            pass  # another account, or a damaged file: fall back to asking
    error = ""
    for _ in range(3):
        answer = ask(
            root, "Unlock Ragnarok", "Enter your passphrase to open your memory.",
            repeat=False, automatic=True, error=error,
        )  # fmt: skip
        if answer is None:
            return None
        try:
            key = keys.unlock_with_passphrase(keystore, answer.passphrase)
        except keys.WrongSecret:
            error = "That is not the right passphrase."
            continue
        if answer.automatic:  # only after the right passphrase, never before
            remember_on_this_account(home, key)
        return key
    return None
