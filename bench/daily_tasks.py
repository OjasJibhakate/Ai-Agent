"""Everyday requests on the real model, checked by what ends up on disk. (The "does it work" test.)

Run: uv run python -m bench.daily_tasks [name-filter]
Each task runs in a throwaway home folder with the same tools the chat has (the screen skill and
real downloads excluded), in the default ask mode with every approval answered yes. A task passes
only if the right thing exists afterwards, not merely if the model said so. Results go to
bench/results/daily_tasks_<date>.json.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ragnarok.memory.ingest import GatewayLlm, Ingestor
from ragnarok.memory.service import MemoryService
from ragnarok.memory.store import MemoryStore
from ragnarok.memory.vault import Vault
from ragnarok.policy.modes import Mode
from ragnarok.runtime.gateway import Gateway
from ragnarok.runtime.loop import Agent, Session, TurnResult
from ragnarok.runtime.tools import memory_tools
from ragnarok.skills.apps import AppEntry, app_tools
from ragnarok.skills.documents import document_tools
from ragnarok.skills.downloads import KNOWN, download_tools
from ragnarok.skills.fileops import file_op_tools
from ragnarok.skills.files import file_tools
from ragnarok.skills.images import image_tools
from ragnarok.skills.shell import shell_tools
from ragnarok.skills.web import web_tools
from tests.doc_fakes import make_pdf

PDF_LINES = [["Interview report", "Reverse engineering round", "Result: strong pass"], ["Page two"]]


class FakeCatalog:
    def __init__(self) -> None:
        self.launched: list[str] = []

    def list_apps(self):
        return [AppEntry("Notepad", "notepad"), AppEntry("Google Chrome", "chrome"),
                AppEntry("Steam", "steam")]  # fmt: skip

    def launch(self, app) -> None:
        self.launched.append(app.name)


class FakeBrowsers:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str]] = []

    def launch(self, browser: str, url: str) -> str:
        self.opened.append((browser, url))
        return "your default browser"


class Bin:
    def __init__(self) -> None:
        self.sent: list[Path] = []

    def __call__(self, path: Path) -> None:
        self.sent.append(path)


@dataclass
class World:
    home: Path
    catalog: FakeCatalog
    browsers: FakeBrowsers
    bin: Bin
    opened: list[Path] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    turns: list[TurnResult] = field(default_factory=list)

    @property
    def tools_used(self) -> list[str]:
        return [t.tool for turn in self.turns for t in turn.trace]

    @property
    def answer(self) -> str:
        return self.turns[-1].text if self.turns else ""

    def files(self, pattern: str = "*") -> list[Path]:
        return [p for p in self.home.rglob(pattern) if p.is_file()]


def read_zip_text(path: Path, member: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(member).decode("utf-8", errors="replace")


# ---- the tasks and their checks ------------------------------------------------------------------


@dataclass
class Task:
    name: str
    prompts: list[str]
    check: Callable[[World], str | None]  # None = pass, otherwise why it failed


def contains(path: Path, *needles: str) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    return all(n.lower() in text for n in needles)


def check_program(ext: str, *needles: str):
    def check(w: World) -> str | None:
        made = w.files(f"*.{ext}")
        if not made:
            return f"no .{ext} file was written (tools: {w.tools_used})"
        if "open_app" in w.tools_used or "use_computer" in w.tools_used:
            return "it opened an app instead of writing the file"
        if not any(contains(p, *needles) for p in made):
            return f"the file does not look like the program (wanted {needles})"
        return None

    return check


def check_pptx(min_slides: int):
    def check(w: World) -> str | None:
        decks = w.files("*.pptx")
        if not decks:
            return f"no presentation was made (tools: {w.tools_used})"
        slides = [
            n for n in zipfile.ZipFile(decks[0]).namelist() if n.startswith("ppt/slides/slide")
        ]
        slides = [n for n in slides if n.endswith(".xml") and "_rels" not in n]
        if len(slides) < min_slides:
            return f"only {len(slides)} slides"
        text = " ".join(read_zip_text(decks[0], n) for n in slides)
        if len(text) < 1500:
            return "the slides have almost no content"
        return None

    return check


def check_xlsx(w: World) -> str | None:
    books = w.files("*.xlsx")
    if not books:
        return f"no spreadsheet was made (tools: {w.tools_used})"
    sheet = read_zip_text(books[0], "xl/worksheets/sheet1.xml")
    if sheet.count("<row ") < 3:
        return "the sheet has fewer than 3 rows"
    return None


def check_docx(*needles: str):
    def check(w: World) -> str | None:
        docs = w.files("*.docx")
        # the fixture's own resume.docx is not "made"
        docs = [d for d in docs if d.name != "resume.docx"]
        if not docs:
            return f"no document was made (tools: {w.tools_used})"
        text = read_zip_text(docs[0], "word/document.xml").lower()
        if not all(n.lower() in text for n in needles):
            return f"the document lacks {needles}"
        return None

    return check


def check_txt_of_pdf(w: World) -> str | None:
    out = w.home / "Downloads" / "interview-report.txt"
    if not out.exists():
        return f"no .txt next to the PDF (tools: {w.tools_used})"
    return None if contains(out, "Reverse engineering") else "the .txt lacks the PDF's text"


def check_folder(w: World) -> str | None:
    found = [p for p in w.home.rglob("*") if p.is_dir() and p.name.lower() == "projects"]
    return None if found else f"no folder called Projects (tools: {w.tools_used})"


def check_folder_and_copy(w: World) -> str | None:
    copied = [p for p in w.home.rglob("report.pdf") if p.parent.name.lower() == "reports"]
    return None if copied else f"report.pdf is not in a Reports folder (tools: {w.tools_used})"


def check_rename(w: World) -> str | None:
    desk = w.home / "Desktop"
    if (desk / "ideas.txt").exists() and not (desk / "notes.txt").exists():
        return None
    return f"notes.txt was not renamed to ideas.txt (tools: {w.tools_used})"


def check_delete(w: World) -> str | None:
    return (
        None
        if any(p.name == "old.txt" for p in w.bin.sent)
        else (f"old.txt was not sent to the Recycle Bin (tools: {w.tools_used})")
    )


def check_listing(w: World) -> str | None:
    return (
        None if "backprop_notes.txt" in w.answer else "the answer does not list the folder's files"
    )


def check_download(w: World) -> str | None:
    pics = w.files("*.jpg")
    return None if pics else f"nothing was downloaded (tools: {w.tools_used})"


def check_steam(w: World) -> str | None:
    return None if w.files("SteamSetup.exe") else f"no installer was saved (tools: {w.tools_used})"


def check_opened(app: str):
    return lambda w: None if app in w.catalog.launched else f"{app} was not opened ({w.tools_used})"


def check_youtube(w: World) -> str | None:
    ok = any("youtube.com" in url and "lofi" in url for _, url in w.browsers.opened)
    return None if ok else f"no YouTube search for lofi ({w.browsers.opened})"


def check_answer(*needles: str):
    def check(w: World) -> str | None:
        low = w.answer.lower()
        return (
            None
            if all(n.lower() in low for n in needles)
            else f"answer lacks {needles}: {w.answer[:100]!r}"
        )

    return check


def check_ran(*needles: str):
    def check(w: World) -> str | None:
        if "run_command" not in w.tools_used:
            return f"it did not run anything (tools: {w.tools_used})"
        out = " ".join(t.result for turn in w.turns for t in turn.trace if t.tool == "run_command")
        return (
            None
            if all(n.lower() in out.lower() for n in needles)
            else f"output lacks {needles}: {out[:120]!r}"
        )

    return check


def check_no_tools(*needles: str):
    def check(w: World) -> str | None:
        if w.tools_used and any(t not in ("memory_search",) for t in w.tools_used):
            return f"it used tools for a plain question: {w.tools_used}"
        return check_answer(*needles)(w) if needles else None

    return check


def check_honest_cannot(w: World) -> str | None:
    low = w.answer.lower()
    claimed = any(
        p in low for p in ("i've set", "alarm is set", "i set", "done", "sent the email", "i sent")
    )
    admits = any(
        p in low for p in ("can't", "cannot", "unable", "not able", "don't have", "no way")
    )
    return None if admits and not claimed else f"it should say it cannot: {w.answer[:120]!r}"


def check_find(w: World) -> str | None:
    return None if "resume" in w.answer.lower() else "the answer does not mention the resume"


def check_pdf_summary(w: World) -> str | None:
    if "read_file" not in w.tools_used:
        return f"it did not read the PDF (tools: {w.tools_used})"
    low = w.answer.lower()
    return (
        None
        if ("interview" in low or "reverse" in low or "strong" in low)
        else f"no summary: {w.answer[:100]!r}"
    )


def check_todo(w: World) -> str | None:
    fixtures = {"notes.txt", "old.txt", "backprop_notes.txt", "cnn_cheatsheet.md", "resume.docx"}
    for p in w.files():
        if p.suffix not in (".txt", ".md", ".docx") or p.name in fixtures:
            continue
        text = (
            read_zip_text(p, "word/document.xml")
            if p.suffix == ".docx"
            else p.read_text("utf-8", "replace")
        )
        if all(word in text.lower() for word in ("milk", "mom", "report")):
            return None
    return f"no file with the three to-do items (tools: {w.tools_used})"


def check_opened_file(*names: str):
    def check(w: World) -> str | None:
        got = [p.name.lower() for p in w.opened]
        if all(any(n.lower() in g for g in got) for n in names):
            return None
        return f"it did not open {names} (opened {got}; tools {w.tools_used})"

    return check


def check_text_file(name_part: str, *needles: str):
    def check(w: World) -> str | None:
        for p in w.files():
            if name_part.lower() in p.name.lower() and p.suffix in (
                ".txt",
                ".md",
                ".html",
                ".docx",
            ):
                text = (
                    read_zip_text(p, "word/document.xml")
                    if p.suffix == ".docx"
                    else p.read_text("utf-8", "replace")
                )
                if all(n.lower() in text.lower() for n in needles):
                    return None
        return f"no '{name_part}' file containing {needles} (tools: {w.tools_used})"

    return check


def check_all(*checks):
    def check(w: World) -> str | None:
        for one in checks:
            why = one(w)
            if why:
                return why
        return None

    return check


def check_two_folders(w: World) -> str | None:
    names = {p.name.lower() for p in w.home.rglob("*") if p.is_dir()}
    return None if {"college", "work"} <= names else f"missing a folder (tools: {w.tools_used})"


def check_moved_into(folder: str, name: str):
    def check(w: World) -> str | None:
        hits = [p for p in w.home.rglob(name) if p.parent.name.lower() == folder.lower()]
        return None if hits else f"{name} is not inside {folder} (tools: {w.tools_used})"

    return check


def check_copied_bare(w: World) -> str | None:
    both = (w.home / "Desktop" / "notes.txt").exists() and (
        w.home / "Documents" / "notes.txt"
    ).exists()
    return None if both else f"notes.txt was not copied to Documents (tools: {w.tools_used})"


def check_moved_to_downloads(w: World) -> str | None:
    moved = (w.home / "Downloads" / "old.txt").exists() and not (
        w.home / "Desktop" / "old.txt"
    ).exists()
    return None if moved else f"old.txt was not moved to Downloads (tools: {w.tools_used})"


def check_answer_any(*needles: str):
    def check(w: World) -> str | None:
        low = w.answer.lower()
        return (
            None
            if any(n.lower() in low for n in needles)
            else f"answer lacks any of {needles}: {w.answer[:100]!r}"
        )

    return check


def check_ran_any_output(w: World) -> str | None:
    if "run_command" not in w.tools_used:
        return f"it did not run anything (tools: {w.tools_used})"
    return None


def check_made_and_ran(ext: str, *needles: str):
    return check_all(check_program(ext, *needles), check_ran_any_output)


TASKS = [
    Task(
        "python program",
        ["make a python program for addition of two numbers"],
        check_program("py", "+"),
    ),
    Task(
        "python, with a follow-up",
        ["make a python program for addition of two numbers", ".py extension"],
        check_program("py", "+"),
    ),
    Task(
        "java program",
        ["make a java program that prints hello world"],
        check_program("java", "class"),
    ),
    Task(
        "c program",
        ["write a c program to find the factorial of a number"],
        check_program("c", "main"),
    ),
    Task("ppt", ["make a ppt on climate change"], check_pptx(4)),
    Task(
        "ppt, 6 slides", ["make a presentation about the solar system with 6 slides"], check_pptx(6)
    ),
    Task("excel", ["make an excel sheet to track my monthly expenses"], check_xlsx),
    Task(
        "word doc",
        ["make a word document with tips for writing a good resume"],
        check_docx("resume"),
    ),
    Task(
        "pdf to txt",
        [
            'make me a txt file of all the contents in "{home}\\Downloads\\interview-report.pdf" this pdf'
        ],
        check_txt_of_pdf,
    ),
    Task("new folder", ["make a new folder called Projects on my desktop"], check_folder),
    Task(
        "folder and copy",
        ["make a new folder called Reports in downloads and copy report.pdf into it"],
        check_folder_and_copy,
    ),
    Task("rename", ["rename notes.txt on my desktop to ideas.txt"], check_rename),
    Task("delete", ["delete old.txt from my desktop"], check_delete),
    Task("list a folder", ["what files are in my deep learning folder"], check_listing),
    Task("find a file", ["find my resume"], check_find),
    Task(
        "read a pdf", ["summarize the pdf interview-report.pdf in my downloads"], check_pdf_summary
    ),
    Task("download images", ["download cat images"], check_download),
    Task("download a program", ["download steam"], check_steam),
    Task("open an app", ["open notepad"], check_opened("Notepad")),
    Task("open youtube", ["open youtube and search lofi beats"], check_youtube),
    Task("plain question", ["what is 15% of 2400"], check_no_tools("360")),
    Task("run a command", ["run python --version"], check_ran("python")),
    Task(
        "write and run",
        ["write a python program that prints the sum of 20 and 22 and run it"],
        check_ran("42"),
    ),
    Task(
        "todo list",
        ["make a todo list file with 3 items: buy milk, call mom, finish report"],
        check_todo,
    ),
    Task("honest: alarm", ["set an alarm for 7am tomorrow"], check_honest_cannot),
    Task(
        "honest: send email",
        ["send an email to my professor saying I will be late"],
        check_honest_cannot,
    ),
]


MORE_TASKS = [
    Task("py file adds", ["create a py file that adds 2 numbers"], check_program("py", "+")),
    Task(
        "prime script",
        ["write me a python script to check if a number is prime"],
        check_program("py", "prime"),
    ),
    Task("calculator", ["make a calculator program in python"], check_program("py", "def ")),
    Task(
        "fibonacci and run",
        ["create a python program for fibonacci series up to 10 terms and run it"],
        check_made_and_ran("py", "fib"),
    ),
    Task(
        "java adds", ["make a java program for adding two numbers"], check_program("java", "class")
    ),
    Task("html page", ["write a html page for my portfolio"], check_program("html", "<html")),
    Task(
        "js file", ["make a javascript file that prints hello"], check_program("js", "console.log")
    ),
    Task("ppt ai", ["generate a ppt about artificial intelligence"], check_pptx(4)),
    Task("ppt ww2 8", ["i need a presentation on world war 2 for class, 8 slides"], check_pptx(8)),
    Task("slides", ["make slides about healthy eating"], check_pptx(4)),
    Task(
        "excel columns",
        ["create an excel with columns name, age, city and 3 sample rows"],
        check_xlsx,
    ),
    Task("budget sheet", ["make a budget spreadsheet for a college student"], check_xlsx),
    Task(
        "cover letter",
        ["write a cover letter for a software internship and save it as a word file"],
        check_docx("internship"),
    ),
    Task(
        "shopping txt",
        ["make a txt file called shopping.txt with milk, eggs, bread"],
        check_text_file("shopping", "milk", "eggs", "bread"),
    ),
    Task(
        "pdf bare name",
        ["convert report.pdf to text"],
        check_all(
            lambda w: (
                None
                if (w.home / "Downloads" / "report.txt").exists()
                else f"no report.txt (tools: {w.tools_used})"
            )
        ),
    ),
    Task("copy bare", ["copy notes.txt to documents"], check_copied_bare),
    Task("move bare", ["move old.txt to the downloads folder"], check_moved_to_downloads),
    Task(
        "two folders",
        ["create a folder named College in documents and another folder named Work in documents"],
        check_two_folders,
    ),
    Task(
        "folder then move",
        ["make me a folder on desktop called Hackathon and put notes.txt inside it"],
        check_moved_into("Hackathon", "notes.txt"),
    ),
    Task("delete bare", ["delete the file old.txt"], check_delete),
    Task("open folder", ["open the folder deep learning"], check_opened_file("deep_learning")),
    Task("open pdf", ["open report.pdf"], check_opened_file("report.pdf")),
    Task(
        "ppt then open",
        ["make a ppt on python programming and then open it"],
        check_all(check_pptx(4), check_opened_file(".pptx")),
    ),
    Task("read a txt", ["what is in notes.txt"], check_answer("milk")),
    Task("count files", ["show me my desktop files"], check_answer("notes.txt")),
    Task("python version", ["check my python version"], check_ran("python")),
    Task("run dir", ["run dir in downloads"], check_ran("report")),
    Task(
        "youtube search",
        ["search python tutorials on youtube"],
        lambda w: (
            None
            if any("youtube" in u and "python" in u for _, u in w.browsers.opened)
            else "no youtube search"
        ),
    ),
    Task("download dogs", ["download 3 pictures of dogs"], check_download),
    Task(
        "download chrome",
        ["download chrome"],
        lambda w: (
            None
            if w.files("ChromeStandaloneSetup64.exe")
            else f"no chrome installer (tools: {w.tools_used})"
        ),
    ),
    Task("greeting", ["hi"], check_no_tools()),
    Task("capital", ["what's the capital of france"], check_no_tools("paris")),
    Task("joke", ["tell me a joke"], check_no_tools()),
    Task("honest: reminder", ["remind me to call mom at 6pm"], check_honest_cannot),
]
TASKS += MORE_TASKS


# ---- running them ------------------------------------------------------------------------------------


def build_home(root: Path) -> Path:
    home = root / "home"
    for folder in ("Desktop", "Documents", "Downloads", "Desktop/Deep_learning"):
        (home / folder).mkdir(parents=True, exist_ok=True)
    (home / "Downloads" / "interview-report.pdf").write_bytes(make_pdf(PDF_LINES))
    (home / "Downloads" / "report.pdf").write_bytes(make_pdf([["Quarterly report"]]))
    (home / "Desktop" / "notes.txt").write_text("remember the milk\n", encoding="utf-8")
    (home / "Desktop" / "old.txt").write_text("stale\n", encoding="utf-8")
    for name in ("backprop_notes.txt", "cnn_cheatsheet.md", "Deep Learning.pdf"):
        (home / "Desktop" / "Deep_learning" / name).write_text("x", encoding="utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "word/document.xml",
            "<w:document xmlns:w='x'><w:body><w:p><w:r><w:t>Jane Doe resume</w:t></w:r></w:p></w:body></w:document>",
        )
    (home / "Documents" / "resume.docx").write_bytes(out.getvalue())
    return home


def fake_internet() -> httpx.MockTransport:
    jpeg = b"\xff\xd8\xff\xe0" + b"j" * 300
    exe = b"MZ" + b"\x90" * 2000

    def serve(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host == "commons.wikimedia.org":
            pages = [
                {
                    "title": f"File:Cat {i}.jpg",
                    "index": i,
                    "imageinfo": [
                        {
                            "mime": "image/jpeg",
                            "thumburl": f"https://upload.wikimedia.org/c/{i}.jpg",
                            "descriptionurl": f"https://commons.wikimedia.org/wiki/File:Cat_{i}.jpg",
                            "extmetadata": {
                                "Artist": {"value": "Someone"},
                                "LicenseShortName": {"value": "CC0"},
                            },
                        }
                    ],
                }
                for i in range(1, 8)
            ]
            return httpx.Response(200, json={"query": {"pages": pages}})
        if request.url.host == "upload.wikimedia.org":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=jpeg)
        if any(url == k.url for k in KNOWN.values()):
            return httpx.Response(
                200, headers={"content-type": "application/octet-stream"}, content=exe
            )
        return httpx.Response(404)

    return httpx.MockTransport(serve)


async def run_task(gw: Gateway, task: Task) -> dict:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        home = build_home(root)
        vault = Vault.open(root / "v.db", os.urandom(32))
        store = MemoryStore(vault)
        store.start()
        svc = MemoryService(store, gw)
        ingestor = Ingestor(store, gw, GatewayLlm(gw))
        world = World(home, FakeCatalog(), FakeBrowsers(), Bin())
        mode = Mode("ask")
        roots = [home / "Desktop", home / "Documents", home / "Downloads"]

        async def approve(name: str, args: dict, decision) -> bool:
            world.approvals.append(name)
            return True  # the owner presses y

        async def public(host: str) -> list[str]:
            return ["93.184.216.34"]

        transport = fake_internet()
        tools = (
            memory_tools(svc, ingestor)
            + app_tools(world.catalog)
            + file_tools(roots, home, mode)
            + file_op_tools(
                roots, home, mode, world.bin, data_dirs=[root / "vault"], opener=world.opened.append
            )
            + shell_tools(home)
            + web_tools(world.browsers)
            + document_tools(home / "Documents")
            + image_tools(transport, home / "Downloads")
            + download_tools(transport, home / "Downloads", public)
        )
        agent = Agent(store, svc, ingestor, gw, profile=mode.profile, approve=approve, tools=tools)
        session = Session()
        started = time.perf_counter()
        error = None
        try:
            for prompt in task.prompts:
                world.turns.append(await agent.turn(session, prompt.replace("{home}", str(home))))
            why = task.check(world)
        except Exception as exc:
            why, error = f"crashed: {type(exc).__name__}: {exc}", repr(exc)
        finally:
            vault.close()
        return {
            "task": task.name, "ok": why is None, "why": why, "seconds": round(time.perf_counter() - started, 1),
            "tools": world.tools_used, "approvals": world.approvals,
            "trace": [(t.tool, t.args, t.result[:160]) for turn in world.turns for t in turn.trace],
            "answer": world.answer[:300], "error": error,
        }  # fmt: skip


async def main(pattern: str = "") -> None:
    gw = Gateway(timeout=300)
    rows = []
    try:
        await gw.warm_up()
        for task in TASKS:
            wanted = [w.strip().lower() for w in pattern.split(",") if w.strip()]
            if wanted and not any(w in task.name.lower() for w in wanted):
                continue
            row = await run_task(gw, task)
            rows.append(row)
            mark = "PASS" if row["ok"] else "FAIL"
            print(
                f"{mark} {row['task']:26s} {row['seconds']:5.1f}s  tools={row['tools']}", flush=True
            )
            if not row["ok"]:
                print(f"       why: {row['why']}\n       said: {row['answer'][:160]!r}", flush=True)
    finally:
        await gw.aclose()
    passed = sum(r["ok"] for r in rows)
    print(f"\n{passed}/{len(rows)} everyday tasks done")
    suffix = "_filtered" if pattern else ""  # a partial run must not overwrite the full results
    out = Path("bench/results") / f"daily_tasks_{datetime.now(UTC):%Y-%m-%d}{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
