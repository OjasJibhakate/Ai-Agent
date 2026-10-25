"""Show the chat window with sample content and save a screenshot of it.
Usage: python -m scripts.preview_chat out.png [plan|typing]
(no option: a chat and a permission card; typing: a message half typed in the input box)"""

import sys
import tkinter as tk

from ragnarok.gui import winapi
from ragnarok.gui.bridge import Bridge
from ragnarok.gui.chat import HEIGHT, WIDTH, ChatWindow
from ragnarok.skills.screen.capture import WindowsScreen

winapi.make_dpi_aware()
root = tk.Tk()
root.withdraw()
chat = ChatWindow(root, Bridge(), on_busy=lambda _b: None, on_close=lambda: None)
chat.handle(
    "welcome", {"model": "qwen3.5:4b", "memories": 12, "gpu": "100% GPU", "first_run": False}
)
if "typing" in sys.argv[2:]:
    chat.entry.insert("1.0", "write a short note about bubble sort and save it to my desktop")
elif "plan" in sys.argv[2:]:
    rows = [
        ("s1", "Read the notes in the folder", [], "R0", False),
        ("s2", "Write the summary document", ["s1"], "R1", True),
        ("s3", "Save it as a PDF", ["s2"], "R1", True),
    ]
    chat.handle("approve", {"type": "plan", "goal": "make a report from my notes", "rows": rows})
else:
    chat.entry.delete("1.0", "end")
    chat.entry.insert("1.0", "what is 15 percent of 2400, and remind me at 6pm to call mom")
    chat._enter(None)  # type: ignore[arg-type]
    chat.handle("assistant_start", {"kind": "bot"})
    chat.handle(
        "assistant_chunk", {"text": "15 percent of 2400 is 360. I set a reminder for 6 pm."}
    )
    chat.handle("footer", {"tools": [("calculator", True), ("set_reminder", True)], "seconds": 1.4})
    chat.handle("idle", {})
    chat.handle(
        "approve",
        {
            "type": "permission",
            "tool": "run_command",
            "args": {"command": "'dir C:\\Users\\me\\Documents'"},
            "note": "This runs a command on your computer.",
        },
    )
x, y = 600, 300
chat.top.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
chat.top.deiconify()
chat.top.lift()
for _ in range(40):
    root.update()
    root.after(20)
frame = WindowsScreen((x, y, x + WIDTH, y + HEIGHT)).grab(max_width=2000)
open(sys.argv[1], "wb").write(frame.png())
print("saved", sys.argv[1], WIDTH, HEIGHT)
root.destroy()
