"""The document skill: what it writes, where, and what it refuses to touch."""

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from ragnarok.policy import permissions
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.loop import Session
from ragnarok.skills import documents
from ragnarok.skills.documents import (
    MAX_CHARS,
    CreateDocumentArgs,
    build_docx,
    build_text,
    document_tools,
    safe_stem,
)
from tests.test_m2_memory import key  # noqa: F401  (fixture)
from tests.test_m4_agent import build, call, kinds, say

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def paragraphs(docx: bytes) -> list[str]:
    """What a reader of the .docx sees: one string per paragraph."""
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        assert z.testzip() is None
        root = ET.fromstring(z.read("word/document.xml"))
    return ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in root.iter(f"{W}p")]


async def make(tmp_path, **args):
    tool = document_tools(tmp_path)[0]
    return await tool.handler(CreateDocumentArgs(**args))


# ---- the .docx itself ---------------------------------------------------------------------------


def test_a_docx_is_a_valid_package_with_the_three_required_parts():
    data = build_docx("Files", "one\ntwo")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["[Content_Types].xml", "_rels/.rels", "word/document.xml"]
        for name in z.namelist():
            ET.fromstring(z.read(name))  # every part is well-formed XML
        types = z.read("[Content_Types].xml").decode()
        assert "wordprocessingml.document.main+xml" in types
        assert 'Target="word/document.xml"' in z.read("_rels/.rels").decode()


def test_title_text_lists_and_blank_lines_come_out_in_order():
    body = "Intro line\n\n# Section\n- first\n* second\nclosing"
    assert paragraphs(build_docx("My Title", body)) == [
        "My Title", "Intro line", "", "Section", "• first", "• second", "closing",
    ]  # fmt: skip


def test_the_title_and_headings_are_bold_and_bigger():
    with zipfile.ZipFile(io.BytesIO(build_docx("T", "# H\nplain"))) as z:
        xml = z.read("word/document.xml").decode()
    assert xml.count("<w:b/>") == 2 and 'w:sz w:val="40"' in xml and 'w:sz w:val="30"' in xml


def test_text_is_escaped_so_it_cannot_break_the_file():
    nasty = 'a < b && c > d "quoted" </w:t></w:p><w:p>injected'
    assert paragraphs(build_docx("T", nasty))[1] == nasty  # shown as text, not read as markup


def test_characters_xml_forbids_are_dropped_and_unicode_is_kept():
    got = paragraphs(build_docx("T", "café → 日本\x00\x01 end"))
    assert got[1] == "café → 日本 end"
    # a vertical tab is a line break to Python's splitlines, so it starts a new paragraph
    assert paragraphs(build_docx("T", "one\x0btwo"))[1:] == ["one", "two"]


def test_the_same_input_gives_the_same_bytes():
    assert build_docx("T", "a\nb") == build_docx("T", "a\nb")
    assert build_docx("T", "a\nb") != build_docx("T", "a\nc")


def test_plain_text_and_markdown_forms():
    assert build_text("Notes", "- a\n- b\n", "md").decode() == "# Notes\n\n- a\n- b\n"
    assert build_text("Notes", "- a\n- b", "txt").decode() == "Notes\n=====\n\n- a\n- b\n"


# ---- where it writes and what it calls the file ----


async def test_a_document_is_created_in_the_ragnarok_folder_of_documents(tmp_path):
    result = await make(tmp_path, title="Deep learning files", content="- a.pdf\n- b.docx")
    path = tmp_path / "Ragnarok" / "Deep learning files.docx"
    assert result.final and result.text == f"Created Deep learning files.docx in {path.parent}."
    assert paragraphs(path.read_bytes()) == ["Deep learning files", "• a.pdf", "• b.docx"]
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["Deep learning files.docx"]


@pytest.mark.parametrize(("fmt", "ext"), [("md", "md"), ("txt", "txt")])
async def test_markdown_and_text_formats(tmp_path, fmt, ext):
    await make(tmp_path, title="Plan", content="one\ntwo", format=fmt)
    assert (
        (tmp_path / "Ragnarok" / f"Plan.{ext}").read_text(encoding="utf-8").endswith("one\ntwo\n")
    )


@pytest.mark.parametrize(
    ("title", "stem"),
    [
        ("My: odd/name?", "My odd name"),
        ("..\\..\\Windows\\evil", "Windows evil"),
        ("CON", "_CON"),
        ("  spaced   out  ", "spaced out"),
        ("x" * 300, "x" * 80),
        ("....", "Document"),
    ],
)
def test_file_names_are_safe_on_windows(title, stem):
    assert safe_stem(title) == stem


async def test_a_title_cannot_walk_out_of_the_folder(tmp_path):
    await make(tmp_path, title="..\\..\\..\\escape", content="x")
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(files) == 1 and files[0].parent == tmp_path / "Ragnarok"


async def test_asking_twice_for_the_same_document_makes_one_file(tmp_path):
    first = await make(tmp_path, title="Report", content="same text")
    again = await make(tmp_path, title="Report", content="same text")
    assert first.text.startswith("Created") and again.text.startswith("Already created")
    assert len(list((tmp_path / "Ragnarok").iterdir())) == 1


async def test_a_different_document_with_the_same_title_never_overwrites_the_first(tmp_path):
    await make(tmp_path, title="Report", content="first version")
    await make(tmp_path, title="Report", content="second version")
    names = sorted(p.name for p in (tmp_path / "Ragnarok").iterdir())
    assert names == ["Report (1).docx", "Report.docx"]
    assert paragraphs((tmp_path / "Ragnarok" / "Report.docx").read_bytes())[1] == "first version"


async def test_a_file_the_user_already_has_is_left_alone(tmp_path):
    folder = tmp_path / "Ragnarok"
    folder.mkdir()
    (folder / "Report.docx").write_bytes(b"the user's own file")
    await make(tmp_path, title="Report", content="new")
    assert (folder / "Report.docx").read_bytes() == b"the user's own file"
    assert (folder / "Report (1).docx").exists()


# ---- crashes and failures ----


async def test_a_crash_while_writing_leaves_no_broken_document_and_the_retry_works(
    tmp_path, monkeypatch
):
    real = documents._write

    def crash_after_starting(path, data):
        path.with_name(path.name + ".part").write_bytes(data[:10])
        raise OSError("power cut")

    monkeypatch.setattr(documents, "_write", crash_after_starting)
    first = await make(tmp_path, title="Report", content="text")
    assert first.startswith("error:") and not list((tmp_path / "Ragnarok").glob("*.docx"))
    monkeypatch.setattr(documents, "_write", real)
    second = await make(tmp_path, title="Report", content="text")  # the retry (kill and resume)
    assert second.text.startswith("Created") and not list((tmp_path / "Ragnarok").glob("*.part"))
    assert paragraphs((tmp_path / "Ragnarok" / "Report.docx").read_bytes())[1] == "text"


async def test_an_unwritable_folder_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "Ragnarok"
    blocker.write_text("a file where the folder should be")
    result = await make(tmp_path, title="Report", content="x")
    assert result.startswith("error:") and "couldn't save" in result


# ---- limits, declaration, permissions ----


def test_arguments_are_validated():
    CreateDocumentArgs(title="Ok", content="x" * MAX_CHARS)
    for bad in (
        {"title": "x", "content": "text"},
        {"title": "Ok", "content": ""},
        {"title": "Ok", "content": "x" * (MAX_CHARS + 1)},
        {"title": "Ok", "content": "text", "format": "exe"},
        {"title": "y" * 121, "content": "text"},
    ):
        with pytest.raises(ValueError):
            CreateDocumentArgs(**bad)


def test_the_tool_declares_everything_the_rules_require(tmp_path):
    tool = document_tools(tmp_path)[0]
    assert tool.name == "create_document" and tool.risk is Risk.R1 and tool.idempotent
    assert tool.sandbox == "workspace-write" and tool.timeout_s > 0 and tool.network == []
    assert tool.fs == {"write": [str(tmp_path / "Ragnarok")]}
    spec = tool.spec()["function"]
    assert set(spec["parameters"]["properties"]) == {"title", "content", "format"}
    assert len(tool.description.split(". ")) <= 2


def test_the_owner_may_create_documents_but_background_runs_must_ask():
    assert permissions.check("interactive", "create_document", Risk.R1).allowed
    assert permissions.check("background", "create_document", Risk.R1).ask
    assert not permissions.check("screen", "create_document", Risk.R1).allowed


async def test_make_a_doc_for_those_files_end_to_end_through_the_agent(tmp_path, key):  # noqa: F811
    vault, store, svc, chat, agent = build(
        tmp_path,
        key,
        [
            call(
                "create_document",
                title="Deep learning files",
                content="- Deep Learning.pdf\n- Neural Nets.docx",
            ),
            say("unused"),
        ],
    )
    agent.tools.update({t.name: t for t in document_tools(tmp_path / "Documents")})
    seen = {}
    real = agent.tools["create_document"].handler

    async def spy(args):
        seen["events"] = kinds(vault)  # what was already durable when the file was written
        return await real(args)

    agent.tools["create_document"].handler = spy
    result = await agent.turn(Session(), "make a doc file for them")
    assert seen["events"] == ["user_message", "memory_tool_call"]  # logged before acting
    assert len(chat.calls) == 1 and result.final_tool
    made = tmp_path / "Documents" / "Ragnarok" / "Deep learning files.docx"
    assert paragraphs(made.read_bytes())[1:] == ["• Deep Learning.pdf", "• Neural Nets.docx"]
    vault.close()
