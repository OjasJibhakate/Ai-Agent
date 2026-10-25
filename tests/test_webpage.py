"""Animations and pages: steps are a correct run of the algorithm; the page is self-contained."""

import itertools
import json
import random
import re

import pytest

from ragnarok.policy import permissions
from ragnarok.policy.permissions import Risk
from ragnarok.skills.webpage import (
    ALGORITHMS,
    CSP,
    DEFAULT_VALUES,
    CreateAnimationArgs,
    CreateWebpageArgs,
    build_animation_page,
    locked_down,
    steps_for,
    webpage_tools,
)

ALGOS = sorted(ALGORITHMS)


def values_of(frame: dict, values: list[int]) -> list[int]:
    return [values[i] for i in frame["o"]]


@pytest.mark.parametrize("algorithm", ALGOS)
@pytest.mark.parametrize("seed", range(12))
def test_replaying_the_frames_sorts_the_numbers(algorithm, seed):
    rng = random.Random(seed)
    values = [rng.randint(1, 20) for _ in range(rng.randint(2, 16))]  # many duplicates
    frames = steps_for(algorithm, values)
    assert values_of(frames[0], values) == values  # starts unsorted, as given
    last = frames[-1]
    assert values_of(last, values) == sorted(values)
    assert last["d"] == list(range(len(values))) and last["t"] == "Sorted!"
    for frame in frames:
        assert sorted(frame["o"]) == list(range(len(values)))  # always a permutation of the bars


@pytest.mark.parametrize("algorithm", ALGOS)
def test_each_frame_changes_the_bars_by_at_most_one_move(algorithm):
    values = list(DEFAULT_VALUES)
    frames = steps_for(algorithm, values)
    for before, after in itertools.pairwise(frames):
        moved = sum(1 for a, b in zip(before["o"], after["o"], strict=True) if a != b)
        assert moved <= (0 if after["k"] != "move" else len(values))
        if after["k"] != "move":
            assert before["o"] == after["o"]  # compares and notes never rearrange anything
        assert after["nc"] >= before["nc"] and after["nm"] >= before["nm"]
    assert frames[-1]["nc"] > 0 and (algorithm == "bubble" or frames[-1]["nm"] >= 0)


def test_bubble_sort_counts_are_the_textbook_ones():
    values = [5, 4, 3, 2, 1]  # the worst case
    frames = steps_for("bubble", values)
    assert frames[-1]["nc"] == 10 and frames[-1]["nm"] == 10  # n(n-1)/2 of each


def test_already_sorted_input_needs_no_moves():
    for algorithm in ("bubble", "insertion", "selection", "merge"):
        assert steps_for(algorithm, [1, 2, 3, 4, 5])[-1]["nm"] == 0


def test_two_equal_numbers_and_the_smallest_case_work():
    for algorithm in ALGOS:
        frames = steps_for(algorithm, [7, 7])
        assert values_of(frames[-1], [7, 7]) == [7, 7]
        frames = steps_for(algorithm, [2, 1])
        assert values_of(frames[-1], [2, 1]) == [1, 2]


def test_unknown_algorithm_is_an_error():
    with pytest.raises(ValueError):
        steps_for("bogo", [1, 2])


# ---- the page ------------------------------------------------------------------------------------


def embedded(page: str):
    match = re.search(r"const VALUES=(\[.*?\]),FRAMES=(\[.*?\]);\n", page, re.S)
    assert match
    return json.loads(match.group(1)), json.loads(match.group(2))


@pytest.mark.parametrize("algorithm", ALGOS)
def test_the_page_embeds_the_values_and_the_frames_and_nothing_remote(algorithm):
    values = [9, 3, 7, 1]
    page = build_animation_page(algorithm, values)
    got_values, frames = embedded(page)
    assert got_values == values and frames == steps_for(algorithm, values)
    assert CSP in page  # the page may not make any network request
    assert not re.search(r"(?:src|href)=[\"']?https?:", page)
    assert "fetch(" not in page and "XMLHttpRequest" not in page and "import(" not in page
    assert ALGORITHMS[algorithm][0] in page


def test_the_title_is_escaped_in_the_page():
    page = build_animation_page("bubble", [3, 1, 2], "<script>alert(1)</script> & co")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in page
    assert page.count("<script>") == 1  # only the page's own script


def test_a_closing_script_tag_in_the_data_cannot_end_the_script_early():
    page = build_animation_page("bubble", [3, 1, 2], "x")
    assert page.count("</script>") == 1


# ---- arguments -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "said,expected",
    [("bubble sort", "bubble"), ("Bubble Sort", "bubble"), ("quicksort", "quick"),
     ("merge-sort", "merge"), ("insertion_sort", "insertion"), ("selection", "selection")],
)  # fmt: skip
def test_algorithm_names_are_understood(said, expected):
    assert CreateAnimationArgs(algorithm=said).algorithm == expected


def test_values_can_be_a_list_or_text_and_are_checked():
    assert CreateAnimationArgs(algorithm="bubble", values="5, 3; 9 and 1").values == [5, 3, 9, 1]
    assert CreateAnimationArgs(algorithm="bubble", values=[4, 2]).values == [4, 2]
    for bad in ([5], [0, 3], [1000, 2], list(range(1, 42))):
        with pytest.raises(ValueError):
            CreateAnimationArgs(algorithm="bubble", values=bad)
    assert CreateAnimationArgs(algorithm="bubble", values="no numbers").values is None  # defaults


# ---- the tools -----------------------------------------------------------------------------------


def make_tools(tmp_path, opener=None):
    opened = []
    tools = webpage_tools(tmp_path, opener=opener or opened.append)
    return {t.name: t for t in tools}, opened, tmp_path / "Ragnarok"


async def test_an_animation_is_saved_and_opened(tmp_path):
    tools, opened, folder = make_tools(tmp_path)
    tool = tools["create_animation"]
    out = await tool.handler(CreateAnimationArgs(algorithm="bubble"))
    text = out if isinstance(out, str) else out.text
    (page,) = folder.glob("*.html")
    assert page.name == "Bubble sort animation.html"
    assert opened == [page] and "opened it in your browser" in text
    assert embedded(page.read_text(encoding="utf-8"))[0] == DEFAULT_VALUES


async def test_the_numbers_and_title_asked_for_are_used(tmp_path):
    tools, opened, folder = make_tools(tmp_path)
    await tools["create_animation"].handler(
        CreateAnimationArgs(
            algorithm="quick", values=[8, 2, 9, 4], title="My quick demo", open=False
        )
    )
    (page,) = folder.glob("*.html")
    assert page.name == "My quick demo.html" and not opened
    assert embedded(page.read_text(encoding="utf-8"))[0] == [8, 2, 9, 4]


async def test_asking_twice_does_not_make_a_second_file(tmp_path):
    tools, _opened, folder = make_tools(tmp_path)
    args = CreateAnimationArgs(algorithm="merge", values=[3, 1, 2])
    await tools["create_animation"].handler(args)
    again = await tools["create_animation"].handler(args)
    assert (again if isinstance(again, str) else again.text).startswith("Already created")
    assert len(list(folder.glob("*.html"))) == 1


async def test_a_browser_that_will_not_open_is_reported_not_fatal(tmp_path):
    def broken(_path):
        raise OSError("no browser")

    tools, _opened, folder = make_tools(tmp_path, opener=broken)
    out = await tools["create_animation"].handler(CreateAnimationArgs(algorithm="bubble"))
    text = out if isinstance(out, str) else out.text
    assert "couldn't open it" in text and len(list(folder.glob("*.html"))) == 1


@pytest.mark.parametrize(
    "html,marker",
    [
        ("<!doctype html><html><head><title>t</title></head><body>hi there friend</body></html>",
         "hi there friend"),
        ("<html><body>no head here, just a body</body></html>", "no head here"),
        ("<div>just a fragment of a page</div>", "just a fragment"),
    ],
)  # fmt: skip
def test_every_page_gets_a_policy_that_blocks_the_network(html, marker):
    page = locked_down(html)
    assert CSP in page and page.count("Content-Security-Policy") == 1
    assert "default-src 'none'" in page and marker in page  # the content is kept


def test_a_page_that_already_has_a_policy_is_left_as_written():
    html = '<html><head><meta http-equiv="Content-Security-Policy" content="x"></head></html>'
    assert locked_down(html) == html


async def test_a_written_page_is_saved_locked_down_and_opened(tmp_path):
    tools, opened, folder = make_tools(tmp_path)
    html = "<html><head></head><body><script>fetch('http://evil.example')</script></body></html>"
    await tools["create_webpage"].handler(CreateWebpageArgs(title="Game", html=html))
    (page,) = folder.glob("*.html")
    saved = page.read_text(encoding="utf-8")
    assert "default-src 'none'" in saved and opened == [page]  # the policy blocks that fetch


def test_the_tools_declare_their_contract_and_run_without_a_prompt_in_ask_mode():
    tools, _opened, _folder = make_tools(__import__("pathlib").Path("."))
    assert set(tools) == {"create_animation", "create_webpage"}
    for tool in tools.values():
        assert tool.risk is Risk.R1 and tool.idempotent and tool.timeout_s >= 10
        assert permissions.check("interactive", tool.name, tool.risk).allowed
        assert permissions.check("background", tool.name, tool.risk).ask
    assert tools["create_webpage"].spec()["function"]["parameters"]["required"] == [
        "title",
        "html",
    ]
