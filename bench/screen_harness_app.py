"""A small Tk window used to test the screen skill end to end. Not part of the product.

Run by bench/screen_eval.py as its own process: `python -m bench.screen_harness_app <scenario> <seed>`.
It prints READY with its window handle and area, then one line for everything the "user" does in it,
so the evaluator can check what really arrived. Scenarios: buttons, search, form, list, scroll.
"""

from __future__ import annotations

import ctypes
import random
import sys
import tkinter as tk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except (OSError, AttributeError):
    pass

FONT = ("Segoe UI", 15)
LABELS = ["Open", "Save", "Export", "Refresh", "Settings", "Share", "Print", "Help"]
FILES = ["report.pdf", "budget.xlsx", "notes.txt", "photo.png", "slides.pptx", "invoice.docx",
         "todo.md", "backup.zip", "data.csv", "logo.svg", "readme.md", "plan.docx"]  # fmt: skip


def emit(*parts: object) -> None:
    print(*parts, flush=True)


def build(root: tk.Tk, scenario: str, rng: random.Random) -> None:
    pad = {"padx": 12, "pady": 10}
    # Real apps show that something happened; so does this one (a status line along the bottom).
    status = tk.StringVar(value="Last action: none yet")
    tk.Label(root, textvariable=status, font=FONT, fg="#1a5fb4").grid(
        row=20, column=0, columnspan=4, sticky="w", **pad
    )

    def report(kind: str, *parts: object) -> None:
        emit(kind, *parts)
        status.set(f"Last action: {kind.lower()} {' '.join(str(p) for p in parts)}")

    if scenario == "buttons":
        labels = LABELS[:]
        rng.shuffle(labels)
        for i, label in enumerate(labels):
            tk.Button(
                root,
                text=label,
                font=FONT,
                width=12,
                height=2,
                command=lambda lb=label: report("CLICK", lb),
            ).grid(row=i // 4 + 1, column=i % 4, **pad)
        tk.Label(root, text="Pick an action", font=FONT).grid(
            row=0, column=0, columnspan=4, pady=14
        )
    elif scenario == "search":
        tk.Label(root, text="Search files", font=FONT).grid(row=0, column=0, **pad)
        entry = tk.Entry(root, font=FONT, width=34)
        entry.grid(row=1, column=0, **pad)
        go = lambda *_: report("SEARCH", entry.get())  # noqa: E731
        entry.bind("<Return>", go)
        tk.Button(root, text="Go", font=FONT, width=8, command=go).grid(row=1, column=1, **pad)
        tk.Button(
            root, text="Settings", font=FONT, width=10, command=lambda: report("CLICK", "Settings")
        ).grid(row=2, column=0, **pad)
    elif scenario == "form":
        tk.Label(root, text="Name", font=FONT).grid(row=0, column=0, sticky="e", **pad)
        name = tk.Entry(root, font=FONT, width=28)
        name.grid(row=0, column=1, **pad)
        tk.Label(root, text="Email", font=FONT).grid(row=1, column=0, sticky="e", **pad)
        email = tk.Entry(root, font=FONT, width=28)
        email.grid(row=1, column=1, **pad)
        sub = tk.IntVar()
        tk.Checkbutton(root, text="Subscribe to newsletter", variable=sub, font=FONT).grid(
            row=2, column=1, sticky="w", **pad
        )
        tk.Button(
            root, text="Submit", font=FONT, width=12,
            command=lambda: report("SUBMIT", name.get(), email.get(), sub.get()),
        ).grid(row=3, column=1, sticky="w", **pad)  # fmt: skip
    elif scenario in ("list", "scroll"):
        items = FILES[:]
        if scenario == "scroll":
            items = [f"item{i:02d}.txt" for i in range(1, 40)] + ["zebra.txt"]
        rng.shuffle(items) if scenario == "list" else None
        box = tk.Listbox(root, font=FONT, width=34, height=10, exportselection=False)
        for item in items:
            box.insert("end", item)
        box.grid(row=0, column=0, **pad)
        if scenario == "list":
            box.bind("<Double-Button-1>", lambda e: report("OPEN", box.get(box.nearest(e.y))))
        else:
            box.bind(
                "<<ListboxSelect>>",
                lambda e: (
                    report("PICK", box.get(box.curselection()[0])) if box.curselection() else None
                ),
            )
    else:
        raise SystemExit(f"unknown scenario {scenario!r}")


def main() -> None:
    scenario, seed = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0
    root = tk.Tk()
    root.title("Ragnarok test app")
    root.geometry("900x560+80+80")
    root.attributes("-topmost", True)
    build(root, scenario, random.Random(seed))
    root.update()
    hwnd = ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)  # the top-level window
    root.after(100, root.focus_force)
    root.update()
    emit(
        "READY",
        hwnd,
        root.winfo_rootx(),
        root.winfo_rooty(),
        root.winfo_width(),
        root.winfo_height(),
    )
    root.after(150_000, root.destroy)  # never outlive a forgotten test
    root.mainloop()


if __name__ == "__main__":
    main()
