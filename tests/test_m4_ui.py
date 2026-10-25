"""The terminal interface: colour on and off, ASCII fallback, aligned boxes, spinner."""

import asyncio
import io
import re

import pytest

from ragnarok.ui import FRAMES, Console, Welcome, visible_len

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


def rule_lines(text: str) -> list[str]:
    """Lines that are nothing but a hairline."""
    return [
        line for line in map(plain, text.splitlines()) if line.strip() and not line.strip("─- ")
    ]


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
def test_banner_box_is_perfectly_aligned_and_fits(first_run, memories):
    for color in (False, True):
        out = Stream()
        c = Console(out, color=color)
        c.banner(welcome(first_run=first_run, memories=memories))
        widths = {len(line) for line in box_lines(out.getvalue())}
        assert len(widths) == 1 and widths.pop() <= c.width
        assert all(visible_len(line) <= c.width for line in out.getvalue().splitlines())
    text = plain(out.getvalue())
    assert "Welcome to Ragnarok" in text or "Welcome back to Ragnarok" in text
    assert ("Welcome back" in text) == (not first_run)
    assert "model" in text and "● encrypted" in text
    assert ("Tips for getting started" in text) == first_run
    assert (f"{memories} memor" + ("y" if memories == 1 else "ies")) in text


@pytest.mark.parametrize("gpu", ["100% GPU", "loading", "offline"])
def test_banner_shows_the_gpu_state_with_a_dot(gpu):
    out = Stream()
    Console(out, color=False).banner(welcome(gpu=gpu))
    assert f"● {gpu}" in out.getvalue()


def test_long_folder_is_shortened_to_fit_the_screen():
    out = Stream()
    c = Console(out, color=False)
    c.banner(welcome(folder="C:\\" + "very\\long\\" * 20 + "Ragnarok"))
    assert all(visible_len(line) <= c.width for line in out.getvalue().splitlines())
    assert "…" in out.getvalue() and out.getvalue().count("Ragnarok") >= 1


def test_falls_back_to_ascii_when_the_console_cannot_print_box_characters():
    out = Stream(encoding="cp1252")
    c = Console(out, color=False)
    assert c.ascii
    c.banner(welcome())
    assert len({len(line) for line in box_lines(out.getvalue())}) == 1
    c.footer([("open_app", True)], 1.0)
    c.memories([_mem(1, "preference", "Prefers tea")])
    c.approval("run_command", {"command": "dir"}, "Runs a program.")
    c.section("Commands")
    c.write(c.prompt("ask mode"))
    out.getvalue().encode("ascii")  # nothing outside ASCII was written


def test_ascii_mode_can_be_forced(monkeypatch):
    monkeypatch.setenv("RAGNAROK_ASCII", "1")
    c = Console(Stream(), color=False)
    assert c.ascii and c.g["prompt"] == ">"


def test_prompt_and_markers():
    c = Console(Stream(), color=False)
    rule, _, marker = c.prompt().partition("\n")
    assert marker == " > " and rule.strip() == "─" * (c.width - 1)  # a hairline above the input
    labelled = c.prompt("ask mode").split("\n")[0]
    assert labelled.endswith(" ask mode ──") and visible_len(labelled) == c.width
    assert c.assistant_marker() == " ● " and c.assistant_marker("done") == " ✓ "
    assert c.assistant_marker("warn") == " ● "
    ascii_c = Console(Stream(), color=False, ascii_only=True)
    assert ascii_c.prompt().endswith("\n > ") and ascii_c.assistant_marker("done") == " + "


def test_footer_lists_tools_and_time_and_marks_blocked_calls():
    out = Stream()
    c = Console(out, color=False)
    c.footer([("open_app", True)], 1.234)
    c.footer([("memory_save", False)], 0.5)
    c.footer([], 0.4)
    lines = out.getvalue().splitlines()
    c.footer([("a", True), ("b", True)], 2.0)
    lines = out.getvalue().splitlines()
    assert lines[0].strip() == "⎿  open_app · 1.2s"
    assert lines[1].strip() == "⎿  ✗ memory_save blocked · 0.5s"
    assert lines[2].strip() == "0.4s"
    assert lines[3].strip() == "⎿  a, b · 2.0s"


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


def test_truecolor_is_used_only_when_asked_for_or_detected(monkeypatch):
    for var in ("COLORTERM", "WT_SESSION", "TERM_PROGRAM", "RAGNAROK_256", "NO_COLOR"):
        monkeypatch.delenv(var, raising=False)
    assert not Console(Stream(), color=True).truecolor  # explicit colour stays on 256 colours
    assert not Console(Stream(tty=True)).truecolor  # nothing in the environment says otherwise
    monkeypatch.setattr("ragnarok.ui._enable_windows_vt", lambda: True)
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert Console(Stream(tty=True)).truecolor
    monkeypatch.setenv("RAGNAROK_256", "1")
    assert not Console(Stream(tty=True)).truecolor
    assert not Console(Stream(), color=False, truecolor=True).truecolor  # no colour, no codes


def test_truecolor_output_is_smooth_and_well_formed():
    out = Stream()
    c = Console(out, color=True, truecolor=True)
    c.banner(welcome())
    c.footer([("open_app", True)], 0.4)
    c.error("bad")
    raw = out.getvalue()
    assert "38;5;" not in raw
    shades = set(re.findall(r"38;2;(\d+;\d+;\d+)", raw))
    assert len(shades) >= 12  # the wordmark and hairline blend rather than step
    assert plain(raw).count("\x1b") == 0
    assert c.ramp(0.0) == "38;2;167;139;250" and c.ramp(1.0) == "38;2;34;211;238"


def test_ramp_falls_back_to_the_256_colour_gradient():
    c = Console(Stream(), color=True)
    assert c.ramp(0.0) == "38;5;141" and c.ramp(1.0) == "38;5;42"


def test_hairline_has_the_asked_width_in_every_mode():
    for kwargs in ({"color": False}, {"color": True}, {"color": True, "truecolor": True}):
        assert visible_len(Console(Stream(), **kwargs).rule(30)) == 30
    assert Console(Stream(), color=False, ascii_only=True).rule(5) == "-----"


def test_permission_card_lists_arguments_and_ends_on_the_prompt():
    out = Stream()
    c = Console(out, color=False)
    prompt = c.approval(
        "run_command", {"command": "x" * 300, "cwd": "C:\\work", "a": 1, "b": 2, "c": 3, "d": 4,
                        "e": 5}, "Runs a program on your computer."
    )  # fmt: skip
    lines = out.getvalue().splitlines()
    assert "Permission needed" in lines[0] and "run_command" in lines[1]
    assert any(line.strip().startswith("│ command") and line.endswith("…") for line in lines)
    assert any("… 1 more" in line for line in lines)
    assert "Runs a program on your computer." in out.getvalue()
    assert all(visible_len(line) <= c.width for line in lines)
    assert prompt.strip() == "╰─ Do you want to proceed? [y/N]"


def test_section_title_has_a_hairline_that_fits():
    out = Stream()
    c = Console(out, color=False)
    c.section("Commands")
    line = out.getvalue().rstrip("\n")
    assert line.startswith(" Commands ─") and visible_len(line) <= c.width + 1


async def test_spinner_sweeps_through_colours_and_shows_the_wait():
    out = Stream(tty=True)
    c = Console(out, color=True, truecolor=True)
    spinner = c.spinner("thinking")
    spinner.start()
    await asyncio.sleep(0.4)
    await spinner.stop("bot")
    assert len(set(re.findall(r"38;2;\d+;\d+;\d+", out.getvalue()))) >= 3


async def test_spinner_animates_then_hands_over_to_the_marker():
    out = Stream(tty=True)
    c = Console(out, color=True)
    spinner = c.spinner("Thinking")
    spinner.start()
    await asyncio.sleep(0.45)
    assert spinner.active and "Thinking…" in plain(out.getvalue()).replace("\r", "\n")
    frames = {ch for ch in plain(out.getvalue()) if ch in FRAMES}
    assert len(frames) >= 2  # the star moved
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
    assert out.getvalue() == " ● "


def test_shimmer_keeps_the_text_and_moves_the_highlight():
    plain_c = Console(Stream(), color=False)
    assert plain_c.shimmer("Thinking…", 3) == "Thinking…"
    for truecolor in (False, True):
        c = Console(Stream(), color=True, truecolor=truecolor)
        a, b = c.shimmer("Thinking…", 1), c.shimmer("Thinking…", 7)
        assert plain(a) == plain(b) == "Thinking…" and a != b


def test_gradient_glow_lights_one_spot():
    c = Console(Stream(), color=True, truecolor=True)
    assert plain(c.gradient("Ragnarok", glow=2)) == "Ragnarok"
    assert c.gradient("Ragnarok", glow=2) != c.gradient("Ragnarok")
    assert c.gradient("Ragnarok", glow=99) == c.gradient("Ragnarok")  # glow far away: no change


async def test_welcome_plays_a_draw_in_then_ends_on_the_same_screen():
    still, moving = Stream(), Stream(tty=True)
    Console(still, color=True).banner(welcome())
    c = Console(moving, color=True, animate=True)
    c.pace = 0  # no waiting in tests
    await c.play_banner(welcome())
    raw = moving.getvalue()
    assert re.search(r"\x1b\[\d+A", raw) and re.search(r"\x1b\[\d+B", raw)  # went back to the name
    final = plain(raw)
    assert "Welcome back to Ragnarok" in final and "100% GPU" in final
    assert visible_len(final.splitlines()[1]) == c.width  # the top edge was drawn in full


async def test_welcome_is_static_when_animation_is_off(monkeypatch):
    monkeypatch.setenv("RAGNAROK_NO_ANIM", "1")
    out = Stream(tty=True)
    c = Console(out, color=True)
    assert not c.animate
    await c.play_banner(welcome())
    assert not re.search(r"\x1b\[\d+[AB]", out.getvalue())


async def test_farewell_closes_the_star_then_leaves_the_message():
    out = Stream(tty=True)
    c = Console(out, color=True, animate=True)
    c.pace = 0
    await c.farewell("See you soon.")
    assert plain(out.getvalue()).replace("\r", "\n").count("See you soon.") >= 5
    assert plain(out.getvalue()).rstrip().endswith("· See you soon.")
    still = Stream()
    await Console(still, color=False).farewell("See you soon.")
    assert still.getvalue().strip() == "See you soon."
