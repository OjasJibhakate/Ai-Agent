"""The terminal interface: colour on and off, ASCII fallback, aligned boxes, spinner."""

import asyncio
import io
import re

import pytest

from ragnarok.ui import Console, Welcome, visible_len

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Stream(io.StringIO):
    """A text stream that can be a terminal and can declare its encoding."""

    def __init__(self, encoding="utf-8", tty=False):
        super().__init__()
        self._encoding = encoding
        self._tty = tty

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return self._tty


def plain(text: str) -> str:
    return ANSI.sub("", text)


def welcome(**over) -> Welcome:
    base = {"model": "qwen3.5:4b", "memories": 14, "gpu": "100% GPU",
            "folder": r"C:\Users\parth\AppData\Local\Ragnarok", "first_run": False}  # fmt: skip
    return Welcome(**{**base, **over})


def box_lines(text: str) -> list[str]:
    lines = [plain(line) for line in text.splitlines()]
    return [line for line in lines if line.strip() and line.strip()[0] in "╭│╰+|"]


def test_colour_is_off_for_pipes_and_files():
    out = Stream()
    c = Console(out)  # not a tty
    c.banner(welcome())
    c.footer([("open_app", True)], 1.2)
    c.error("nope")
    assert "\x1b" not in out.getvalue()


def test_colour_codes_appear_when_enabled_and_use_several_hues():
    out = Stream()
    c = Console(out, color=True)
    c.banner(welcome())
    c.footer([("open_app", True), ("memory_save", False)], 0.4)
    c.memories([_mem(1, "preference", "Prefers tea"), _mem(2, "project", "Billing uses Python")])
    c.error("bad")
    c.success("fine")
    hues = set(re.findall(r"38;5;(\d+)", out.getvalue()))
    assert len(hues) >= 8  # gradient title, steel border, teal model, amber memory, green, coral...
    assert plain(out.getvalue()).count("\x1b") == 0  # every code is well-formed


def test_no_color_environment_variable_wins(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = Stream(tty=True)
    c = Console(out)
    c.banner(welcome())
    assert "\x1b" not in out.getvalue()


@pytest.mark.parametrize("first_run", [True, False])
@pytest.mark.parametrize("memories", [0, 1, 14])
def test_banner_box_is_perfectly_aligned(first_run, memories):
    for color in (False, True):
        out = Stream()
        c = Console(out, color=color)
        c.banner(welcome(first_run=first_run, memories=memories))
        widths = {len(line) for line in box_lines(out.getvalue())}
        assert len(widths) == 1, widths
    text = plain(out.getvalue())
    assert ("Welcome." in text) == first_run and ("Welcome back." in text) == (not first_run)
    assert (f"{memories} memor" + ("y" if memories == 1 else "ies")) in text


def test_long_folder_is_shortened_inside_the_box():
    out = Stream()
    c = Console(out, color=False)
    c.banner(welcome(folder="C:\\" + "very\\long\\" * 20 + "Ragnarok"))
    widths = {len(line) for line in box_lines(out.getvalue())}
    assert (
        len(widths) == 1
        and "…" in out.getvalue()
        and out.getvalue().rstrip().count("Ragnarok") >= 1
    )


def test_falls_back_to_ascii_when_the_console_cannot_print_box_characters():
    out = Stream(encoding="cp1252")
    c = Console(out, color=False)
    assert c.ascii
    c.banner(welcome())
    c.footer([("open_app", True)], 1.0)
    c.memories([_mem(1, "preference", "Prefers tea")])
    out.getvalue().encode("ascii")  # nothing outside ASCII was written
    assert len({len(line) for line in box_lines(out.getvalue())}) == 1


def test_ascii_mode_can_be_forced(monkeypatch):
    monkeypatch.setenv("RAGNAROK_ASCII", "1")
    c = Console(Stream(), color=False)
    assert c.ascii and c.g["prompt"] == ">"


def test_prompt_and_markers():
    c = Console(Stream(), color=False)
    assert c.prompt() == " › "
    assert c.assistant_marker() == " ◆ " and c.assistant_marker("done") == " ✓ "
    assert c.assistant_marker("warn") == " ◆ "
    ascii_c = Console(Stream(), color=False, ascii_only=True)
    assert ascii_c.prompt() == " > " and ascii_c.assistant_marker("done") == " + "


def test_footer_lists_tools_and_time_and_marks_blocked_calls():
    out = Stream()
    c = Console(out, color=False)
    c.footer([("open_app", True)], 1.234)
    c.footer([("memory_save", False)], 0.5)
    c.footer([], 0.4)
    lines = out.getvalue().splitlines()
    assert lines[0].strip() == "↳ open_app · 1.2s"
    assert lines[1].strip() == "✗ memory_save blocked · 0.5s"
    assert lines[2].strip() == "0.4s"


def _mem(i, kind, text):
    from types import SimpleNamespace

    return SimpleNamespace(id=i, memory_type=kind, content=text)


def test_memory_list_formats_ids_types_and_truncates():
    out = Stream()
    c = Console(out, color=False)
    c.memories([])
    assert "Nothing remembered yet" in out.getvalue()
    out.truncate(0)
    out.seek(0)
    c.memories([_mem(3, "preference", "Prefers tea"), _mem(12, "project", "x" * 300)])
    lines = out.getvalue().splitlines()
    assert any(line.strip().startswith("m3") and "preference" in line for line in lines)
    assert all(visible_len(line) <= c.width for line in lines)
    assert any(line.rstrip().endswith("…") for line in lines)
    assert "2 shown" in out.getvalue()


def test_help_lists_every_command():
    out = Stream()
    Console(out, color=False).help()
    for cmd in ("/memories", "/profile", "/forget", "/clear", "/quit"):
        assert cmd in out.getvalue()


def test_box_edges_line_up():
    out = Stream()
    Console(out, color=False).box("Recovery code", ["", "4SR6-D6JC-6RV5-NZ6I", ""])
    assert len({len(line) for line in out.getvalue().splitlines() if line}) == 1


def test_gradient_title_keeps_the_text():
    c = Console(Stream(), color=True)
    assert plain(c.gradient("Ragnarok")) == "Ragnarok"
    assert Console(Stream(), color=False).gradient("Ragnarok") == "Ragnarok"


async def test_spinner_animates_then_hands_over_to_the_marker():
    out = Stream(tty=True)
    c = Console(out, color=True)
    spinner = c.spinner("thinking")
    spinner.start()
    await asyncio.sleep(0.3)
    assert spinner.active and "thinking" in out.getvalue()
    frames = {ch for ch in plain(out.getvalue()) if ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"}
    assert len(frames) >= 2  # it moved
    await spinner.stop("done")
    assert not spinner.active
    tail = out.getvalue().split("\r\x1b[K")[-1]
    assert plain(tail) == " ✓ "  # the line was cleared and replaced by the marker


async def test_spinner_is_silent_without_a_colour_terminal():
    out = Stream(tty=False)
    c = Console(out, color=False)
    spinner = c.spinner()
    spinner.start()
    await asyncio.sleep(0.1)
    assert out.getvalue() == ""  # nothing animated into a log file
    await spinner.stop("bot")
    assert out.getvalue() == " ◆ "
