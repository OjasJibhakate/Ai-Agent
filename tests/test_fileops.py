"""Make, copy, move, edit and delete files; PDF to text; and how the modes treat each of them."""

import io
import os
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from ragnarok.policy.modes import Mode
from ragnarok.policy.permissions import Risk
from ragnarok.runtime.loop import Session
from ragnarok.runtime.tools import CURRENT_TEXT
from ragnarok.skills import fileops
from ragnarok.skills.documents import build_docx
from ragnarok.skills.fileops import file_op_tools
from tests.doc_fakes import make_pdf, make_pptx
from tests.test_m2_memory import key  # noqa: F401  (fixture)
from tests.test_m4_agent import build, call, kinds, say

PDF_PAGES = [["Interview report", "Reverse engineering round"], ["Page two: results (good)"]]


@pytest.fixture()
def world(tmp_path):
    home = tmp_path / "home"
    for folder in ("Desktop", "Documents", "Downloads", "dev", ".ssh"):
        (home / folder).mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "vault.db").write_bytes(b"secret")
    return home, vault


class Bin:
    """A recycler that records what would have gone to the Recycle Bin."""

    def __init__(self):
        self.sent: list[Path] = []

    def __call__(self, path: Path) -> None:
        self.sent.append(path)


def tools_for(world, mode=None, recycler=None):
    home, vault = world
    roots = [home / "Desktop", home / "Documents", home / "Downloads"]
    tools = file_op_tools(roots, home, mode, recycler or Bin(), data_dirs=[vault])
    return {t.name: t for t in tools}


async def use(tools, name, text="", **args):
    token = CURRENT_TEXT.set(text)
    try:
        result = await tools[name].handler(tools[name].args_model(**args))
    finally:
        CURRENT_TEXT.reset(token)
    return getattr(result, "text", result)


# ---- declarations ------------------------------------------------------------------------------


def test_every_tool_declares_what_the_rules_require_and_the_risks_are_as_designed(world):
    tools = tools_for(world)
    risks = {name: t.risk for name, t in tools.items()}
    assert risks == {
        "make_folder": Risk.R1, "create_file": Risk.R1, "copy_path": Risk.R1,
        "extract_text": Risk.R1, "edit_file": Risk.R2, "move_path": Risk.R2, "delete_path": Risk.R3,
        "open_path": Risk.R1,
    }  # fmt: skip
    for name, tool in tools.items():
        # opening a file starts another program as the user, so it is declared full-access
        assert tool.sandbox == ("full-access" if name == "open_path" else "workspace-write")
        assert tool.timeout_s > 0 and tool.fs
        assert isinstance(tool.idempotent, bool) and tool.network == []
        assert len(tool.description.split(". ")) <= 2, tool.name
        assert tool.spec()["function"]["parameters"]["properties"]
    assert "Recycle Bin" in tools["delete_path"].approval_note


def test_tool_names_do_not_clash_with_the_other_skills(world):
    from ragnarok.skills.documents import document_tools
    from ragnarok.skills.downloads import download_tools
    from ragnarok.skills.files import file_tools
    from ragnarok.skills.images import image_tools
    from ragnarok.skills.shell import shell_tools
    from ragnarok.skills.web import web_tools

    home, _ = world
    names = [t.name for t in file_op_tools([home], home)]
    for other in (file_tools([home], home), shell_tools(home), web_tools(), document_tools(home),
                  image_tools(downloads=home), download_tools(downloads=home)):  # fmt: skip
        names += [t.name for t in other]
    assert len(names) == len(set(names))


# ---- make_folder and create_file ----------------------------------------------------------------


async def test_make_a_folder_even_several_levels_deep(world):
    home, _ = world
    out = await use(tools_for(world), "make_folder", path="Desktop/projects/ml/week1")
    assert (home / "Desktop" / "projects" / "ml" / "week1").is_dir() and out.startswith("Created")
    again = await use(tools_for(world), "make_folder", path="Desktop/projects/ml/week1")
    assert again.startswith("The folder already exists")


async def test_a_folder_cannot_replace_a_file_or_go_somewhere_off_limits(world):
    home, vault = world
    (home / "Desktop" / "f.txt").write_text("x")
    assert "is a file" in await use(tools_for(world), "make_folder", path="Desktop/f.txt")
    for bad in (".ssh/new", str(vault / "x"), "C:/Windows/new", "D:/"):
        assert (await use(tools_for(world), "make_folder", path=bad)).startswith("error:"), bad


async def test_create_a_file_with_the_text_given(world):
    home, _ = world
    code = "a = int(input())\nb = int(input())\nprint(a + b)\n"
    out = await use(tools_for(world), "create_file", path="dev/add.py", content=code)
    assert (home / "dev" / "add.py").read_text() == code and out.startswith("Created")


async def test_create_file_never_replaces_and_makes_missing_folders(world):
    home, _ = world
    (home / "dev" / "keep.txt").write_text("mine")
    out = await use(tools_for(world), "create_file", path="dev/keep.txt", content="new")
    assert out.startswith("error:") and "already exists" in out
    assert (home / "dev" / "keep.txt").read_text() == "mine"
    await use(tools_for(world), "create_file", path="dev/sub/deeper/a.txt", content="x")
    assert (home / "dev" / "sub" / "deeper" / "a.txt").exists()


async def test_a_crash_while_creating_leaves_no_broken_file(world, monkeypatch):
    home, _ = world

    def crash(path, data):
        path.with_name(path.name + ".part").write_bytes(data[:2])
        raise OSError("disk full")

    monkeypatch.setattr(fileops, "_write_atomic", crash)
    out = await use(tools_for(world), "create_file", path="dev/a.txt", content="hello")
    assert out.startswith("error:") and not (home / "dev" / "a.txt").exists()


async def test_files_that_run_by_themselves_need_full_mode(world):
    home, _ = world
    name = "Documents/WindowsPowerShell/profile.ps1"
    out = await use(tools_for(world, Mode("edits")), "create_file", path=name, content="calc")
    assert out.startswith("error:") and "/mode full" in out and not (home / name).exists()
    await use(tools_for(world, Mode("full")), "create_file", path=name, content="# mine")
    assert (home / name).read_text() == "# mine"


# ---- edit_file ----


async def test_edit_replaces_one_exact_piece(world):
    home, _ = world
    f = home / "dev" / "a.py"
    f.write_text("x = 1\ny = 2\n")
    out = await use(
        tools_for(world), "edit_file", path="dev/a.py", old_text="y = 2", new_text="y = 3"
    )
    assert f.read_text() == "x = 1\ny = 3\n" and "replaced 1 piece" in out


@pytest.mark.parametrize(
    ("content", "old", "problem"),
    [("a a", "a", "exactly once"), ("abc", "zzz", "exactly once"),
     ("abc", "", "exactly once")],
)  # fmt: skip
async def test_edit_needs_the_text_to_appear_exactly_once(world, content, old, problem):
    home, _ = world
    (home / "dev" / "a.txt").write_text(content)
    out = await use(tools_for(world), "edit_file", path="dev/a.txt", old_text=old, new_text="X")
    assert out.startswith("error:") and problem in out
    assert (home / "dev" / "a.txt").read_text() == content


async def test_edit_without_old_text_replaces_the_whole_file(world):
    home, _ = world
    (home / "dev" / "a.txt").write_text("old")
    out = await use(tools_for(world), "edit_file", path="dev/a.txt", new_text="brand new")
    assert (home / "dev" / "a.txt").read_text() == "brand new" and "whole file" in out


async def test_edit_refuses_binary_big_missing_and_off_limits_files(world):
    home, vault = world
    (home / "dev" / "pic.bin").write_bytes(b"\x00\x01\x02" * 100)
    (home / "dev" / "doc.docx").write_bytes(build_docx("t", "x"))
    (home / "dev" / "big.txt").write_text("x" * (fileops.MAX_EDIT_BYTES + 1))
    for path in (
        "dev/pic.bin",
        "dev/doc.docx",
        "dev/big.txt",
        "dev/nope.txt",
        str(vault / "vault.db"),
    ):
        out = await use(tools_for(world), "edit_file", path=path, new_text="x")
        assert out.startswith("error:"), path
    assert (vault / "vault.db").read_bytes() == b"secret"


# ---- copy_path and move_path ----


async def test_copy_a_file_into_a_folder_or_to_a_new_name(world):
    home, _ = world
    (home / "Downloads" / "r.pdf").write_bytes(b"data")
    (home / "Documents" / "Reports").mkdir()
    await use(
        tools_for(world), "copy_path", source="Downloads/r.pdf", destination="Documents/Reports"
    )
    await use(
        tools_for(world), "copy_path", source="Downloads/r.pdf", destination="Desktop/copy.pdf"
    )
    assert (home / "Documents" / "Reports" / "r.pdf").read_bytes() == b"data"
    assert (home / "Desktop" / "copy.pdf").read_bytes() == b"data"
    assert (home / "Downloads" / "r.pdf").exists()  # a copy leaves the original


async def test_copy_a_whole_folder(world):
    home, _ = world
    (home / "Desktop" / "proj" / "sub").mkdir(parents=True)
    (home / "Desktop" / "proj" / "a.txt").write_text("a")
    (home / "Desktop" / "proj" / "sub" / "b.txt").write_text("b")
    out = await use(
        tools_for(world), "copy_path", source="Desktop/proj", destination="Documents/proj2"
    )
    assert (
        home / "Documents" / "proj2" / "sub" / "b.txt"
    ).read_text() == "b" and "Copied the folder" in out


async def test_copy_never_replaces_and_never_nests_a_folder_in_itself(world):
    home, _ = world
    (home / "Desktop" / "a").mkdir()
    (home / "Desktop" / "a.txt").write_text("1")
    (home / "Documents" / "a.txt").write_text("2")
    out = await use(
        tools_for(world), "copy_path", source="Desktop/a.txt", destination="Documents/a.txt"
    )
    assert "already exists" in out and (home / "Documents" / "a.txt").read_text() == "2"
    out = await use(
        tools_for(world), "copy_path", source="Desktop/a", destination="Desktop/a/inner"
    )
    assert out.startswith("error:") and "inside itself" in out


async def test_copy_needs_the_destination_folder_to_exist(world):
    home, _ = world
    (home / "Desktop" / "a.txt").write_text("1")
    out = await use(
        tools_for(world), "copy_path", source="Desktop/a.txt", destination="Desktop/new/a.txt"
    )
    assert "does not exist; make it first" in out


async def test_make_a_folder_then_put_the_file_in_it(world):
    # "make a new folder and paste that file in it"
    home, _ = world
    (home / "Downloads" / "report.pdf").write_bytes(b"%PDF")
    await use(tools_for(world), "make_folder", path="Downloads/Interview")
    await use(
        tools_for(world),
        "copy_path",
        source="Downloads/report.pdf",
        destination="Downloads/Interview",
    )
    assert (home / "Downloads" / "Interview" / "report.pdf").read_bytes() == b"%PDF"


async def test_reading_scope_depends_on_the_mode_but_writing_always_stays_out_of_bad_places(world):
    home, vault = world
    (home / "dev" / "code.py").write_text("print(1)")
    ask = tools_for(world, Mode("ask"))
    out = await use(ask, "copy_path", source="dev/code.py", destination="Desktop/code.py")
    assert out.startswith("error:") and "/mode edits" in out
    wide = tools_for(world, Mode("edits"))
    await use(wide, "copy_path", source="dev/code.py", destination="Desktop/code.py")
    assert (home / "Desktop" / "code.py").exists()
    for mode in ("ask", "edits", "full"):  # the vault and saved logins are not copyable in any mode
        t = tools_for(world, Mode(mode))
        assert (
            await use(t, "copy_path", source=str(vault / "vault.db"), destination="Desktop/v.db")
        ).startswith("error:")
        assert (await use(t, "copy_path", source=".ssh", destination="Desktop/ssh")).startswith(
            "error:"
        )


async def test_move_and_rename(world):
    home, _ = world
    (home / "Downloads" / "a.txt").write_text("x")
    (home / "Documents" / "Archive").mkdir()
    await use(
        tools_for(world), "move_path", source="Downloads/a.txt", destination="Documents/Archive"
    )
    assert (home / "Documents" / "Archive" / "a.txt").exists() and not (
        home / "Downloads" / "a.txt"
    ).exists()
    await use(
        tools_for(world),
        "move_path",
        source="Documents/Archive/a.txt",
        destination="Documents/Archive/b.txt",
    )
    assert (home / "Documents" / "Archive" / "b.txt").read_text() == "x"


async def test_move_refuses_to_replace_or_to_move_what_must_stay(world):
    home, vault = world
    (home / "Desktop" / "a.txt").write_text("1")
    (home / "Desktop" / "b.txt").write_text("2")
    assert "already exists" in await use(
        tools_for(world), "move_path", source="Desktop/a.txt", destination="Desktop/b.txt"
    )
    for bad in (
        "Desktop",
        "Documents",
        "Downloads",
        str(home),
        str(vault),
        ".ssh",
        "Desktop/nothing",
    ):
        out = await use(tools_for(world), "move_path", source=bad, destination="Documents/x")
        assert out.startswith("error:"), bad
    assert (home / "Desktop").is_dir() and (vault / "vault.db").exists()


# ---- delete_path (Recycle Bin only) ----


async def test_delete_goes_to_the_recycle_bin_and_says_so(world):
    home, _ = world
    bin_ = Bin()
    (home / "Desktop" / "old.txt").write_text("x")
    out = await use(tools_for(world, recycler=bin_), "delete_path", path="Desktop/old.txt")
    assert bin_.sent == [home / "Desktop" / "old.txt"] and "Recycle Bin" in out


async def test_delete_refuses_what_must_never_go(world):
    home, vault = world
    bin_ = Bin()
    tools = tools_for(world, Mode("full"), bin_)  # even in full mode
    for bad in (str(home), "Desktop", "Documents", "Downloads", str(vault), str(vault / "vault.db"),
                ".ssh", "C:/Windows", "C:/", "Desktop/missing.txt"):  # fmt: skip
        assert (await use(tools, "delete_path", path=bad)).startswith("error:"), bad
    assert bin_.sent == []


async def test_a_failed_recycle_is_reported_not_hidden(world):
    home, _ = world
    (home / "Desktop" / "x.txt").write_text("x")

    def refuses(path):
        raise fileops.FileOpError("Windows would not move that to the Recycle Bin (code 5)")

    out = await use(tools_for(world, recycler=refuses), "delete_path", path="Desktop/x.txt")
    assert out.startswith("error:") and "Recycle Bin" in out


@pytest.mark.skipif(os.name != "nt", reason="the Recycle Bin is Windows only")
def test_the_real_recycle_bin_call_works(tmp_path):
    # one real call: a uniquely named empty file goes to the bin; then it is removed from the bin
    import subprocess

    name = f"ragnarok_recycle_test_{os.getpid()}.txt"
    victim = tmp_path / name
    victim.write_text("x")
    fileops.send_to_recycle_bin(victim)
    assert not victim.exists()
    cleanup = (
        "$bin = (New-Object -ComObject Shell.Application).NameSpace(10); "
        f"$bin.Items() | Where-Object {{ $_.Name -like 'ragnarok_recycle_test_{os.getpid()}*' }} | "
        "ForEach-Object { Remove-Item -LiteralPath $_.Path -Force }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", cleanup], check=False, timeout=60)


# ---- extract_text: PDF, Word, PowerPoint to .txt ----


async def test_a_pdf_becomes_a_txt_next_to_it(world):
    home, _ = world
    (home / "Downloads" / "report.pdf").write_bytes(make_pdf(PDF_PAGES))
    out = await use(tools_for(world), "extract_text", source="Downloads/report.pdf")
    text = (home / "Downloads" / "report.txt").read_text(encoding="utf-8")
    assert "Interview report" in text and "Reverse engineering round" in text
    assert "--- page 2 ---" in text and "Page two: results (good)" in text
    assert "2 pages" in out and str(home / "Downloads" / "report.txt") in out


async def test_the_owners_exact_request_with_a_full_path(world):
    home, _ = world
    pdf = home / "Downloads" / "interview-report-reverse-engineer-2026-10-22.pdf"
    pdf.write_bytes(make_pdf(PDF_PAGES))
    request = f'make me a txt file of all the contents in "{pdf}" this pdf'
    out = await use(tools_for(world), "extract_text", request, source=str(pdf))
    assert "Saved the text of interview-report-reverse-engineer-2026-10-22.pdf" in out
    assert pdf.with_suffix(".txt").read_text(encoding="utf-8").count("--- page") == 2


async def test_choose_where_the_txt_goes(world):
    home, _ = world
    (home / "Downloads" / "r.pdf").write_bytes(make_pdf(PDF_PAGES))
    (home / "Documents" / "Texts").mkdir()
    await use(
        tools_for(world), "extract_text", source="Downloads/r.pdf", destination="Documents/Texts"
    )
    await use(
        tools_for(world), "extract_text", source="Downloads/r.pdf", destination="Desktop/mine.txt"
    )
    assert (home / "Documents" / "Texts" / "r.txt").exists() and (
        home / "Desktop" / "mine.txt"
    ).exists()
    await use(
        tools_for(world), "extract_text", source="Downloads/r.pdf", destination="Desktop/noext"
    )
    assert (home / "Desktop" / "noext.txt").exists()


async def test_extracting_twice_does_not_duplicate_and_never_overwrites(world):
    home, _ = world
    (home / "Downloads" / "r.pdf").write_bytes(make_pdf(PDF_PAGES))
    first = await use(tools_for(world), "extract_text", source="Downloads/r.pdf")
    again = await use(tools_for(world), "extract_text", source="Downloads/r.pdf")
    assert first.startswith("Saved") and again.startswith("Already saved")
    (home / "Downloads" / "r.txt").write_text("my own notes")  # something else is there
    third = await use(tools_for(world), "extract_text", source="Downloads/r.pdf")
    assert (home / "Downloads" / "r.txt").read_text() == "my own notes"
    assert (home / "Downloads" / "r (1).txt").exists() and "r (1).txt" in third


async def test_word_and_powerpoint_files_work_too(world):
    home, _ = world
    (home / "Documents" / "plan.docx").write_bytes(build_docx("The plan", "- step one\nstep two"))
    (home / "Documents" / "talk.pptx").write_bytes(
        make_pptx([["Title slide"], ["Point A", "Point B"]])
    )
    await use(tools_for(world), "extract_text", source="Documents/plan.docx")
    await use(tools_for(world), "extract_text", source="Documents/talk.pptx")
    assert (home / "Documents" / "plan.txt").read_text(encoding="utf-8").splitlines() == [
        "The plan", "\u2022 step one", "step two",
    ]  # fmt: skip
    slides = (home / "Documents" / "talk.txt").read_text(encoding="utf-8")
    assert "--- slide 1 ---\nTitle slide" in slides and "Point A\nPoint B" in slides


async def test_documents_that_cannot_be_read_are_explained(world):
    home, _ = world
    d = home / "Downloads"
    (d / "scan.pdf").write_bytes(make_pdf([[]], scanned=True))
    (d / "fake.pdf").write_bytes(b"not a pdf at all")
    (d / "broken.docx").write_bytes(b"PK\x03\x04junk")
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(make_pdf(PDF_PAGES))))
    writer.encrypt("secret-password")
    with (d / "locked.pdf").open("wb") as f:
        writer.write(f)
    expected = {"scan.pdf": "scanned", "fake.pdf": "couldn't read", "broken.docx": "couldn't read",
                "locked.pdf": "password-protected"}  # fmt: skip
    for name, why in expected.items():
        out = await use(tools_for(world), "extract_text", source=f"Downloads/{name}")
        assert out.startswith("error:") and why in out, (name, out)
        assert not (d / (Path(name).stem + ".txt")).exists()


async def test_extract_respects_the_guard_and_the_mode(world):
    home, vault = world
    (home / "dev" / "x.pdf").write_bytes(make_pdf(PDF_PAGES))
    out = await use(tools_for(world, Mode("ask")), "extract_text", source="dev/x.pdf")
    assert out.startswith("error:") and "/mode edits" in out  # outside Desktop/Documents/Downloads
    await use(tools_for(world, Mode("edits")), "extract_text", source="dev/x.pdf")
    assert (home / "dev" / "x.txt").exists()
    (vault / "a.pdf").write_bytes(make_pdf(PDF_PAGES))
    for mode in ("ask", "edits", "full"):
        out = await use(tools_for(world, Mode(mode)), "extract_text", source=str(vault / "a.pdf"))
        assert out.startswith("error:") and "vault" in out


# ---- one tool call or several: when the turn may end ----


async def test_a_single_step_request_ends_with_the_tools_own_sentence(world):
    home, _ = world
    token = CURRENT_TEXT.set("make a folder called Reports on the desktop")
    try:
        result = await tools_for(world)["make_folder"].handler(
            tools_for(world)["make_folder"].args_model(path="Desktop/Reports")
        )
    finally:
        CURRENT_TEXT.reset(token)
    assert getattr(result, "final", False) is True


async def test_a_request_with_more_steps_hands_control_back_to_the_model(world):
    home, _ = world
    token = CURRENT_TEXT.set("make a new folder and paste that file in it")
    try:
        tool = tools_for(world)["make_folder"]
        result = await tool.handler(tool.args_model(path="Desktop/Reports"))
    finally:
        CURRENT_TEXT.reset(token)
    assert isinstance(result, str) and result.startswith("Created")  # plain text: the turn goes on


# ---- through the real agent loop, in each mode ----


def agent_with(tmp_path, key, world, replies, mode, approve=None, bin_=None):  # noqa: F811
    home, vault = world
    vault_, store, svc, chat, agent = build(
        tmp_path, key, replies, profile=mode.profile, approve=approve
    )
    roots = [home / "Desktop", home / "Documents", home / "Downloads"]
    tools = file_op_tools(roots, home, mode, bin_ or Bin(), data_dirs=[vault])
    agent.tools.update({t.name: t for t in tools})
    return vault_, chat, agent


async def test_the_owners_pdf_request_end_to_end(tmp_path, key, world):  # noqa: F811
    home, _ = world
    pdf = home / "Downloads" / "interview-report.pdf"
    pdf.write_bytes(make_pdf(PDF_PAGES))
    vault_, chat, agent = agent_with(
        tmp_path, key, world, [call("extract_text", source=str(pdf)), say("unused")], Mode("ask")
    )
    seen = {}
    real = agent.tools["extract_text"].handler

    async def spy(args):
        seen["events"] = kinds(vault_)
        return await real(args)

    agent.tools["extract_text"].handler = spy
    result = await agent.turn(
        Session(), f'make me a txt file of all the contents in "{pdf}" this pdf'
    )
    assert seen["events"] == ["user_message", "memory_tool_call"]  # logged before acting
    assert len(chat.calls) == 1 and result.final_tool and "Saved the text of" in result.text
    assert "Reverse engineering round" in pdf.with_suffix(".txt").read_text(encoding="utf-8")
    vault_.close()


async def test_new_folder_and_paste_the_file_in_it_end_to_end(tmp_path, key, world):  # noqa: F811
    home, _ = world
    (home / "Downloads" / "report.pdf").write_bytes(b"%PDF-1.4")
    replies = [
        call("make_folder", path="Downloads/Interview"),
        call("copy_path", source="Downloads/report.pdf", destination="Downloads/Interview"),
        say("Done: the file is in the new Interview folder."),
    ]
    vault_, chat, agent = agent_with(tmp_path, key, world, replies, Mode("ask"))
    result = await agent.turn(Session(), "make a new folder and paste that file in it")
    assert (home / "Downloads" / "Interview" / "report.pdf").exists()
    assert [t.tool for t in result.trace] == ["make_folder", "copy_path"]
    assert result.text.startswith("Done")  # the model got to finish the sentence
    vault_.close()


async def test_ask_mode_asks_before_moving_or_deleting(tmp_path, key, world):  # noqa: F811
    home, _ = world
    (home / "Desktop" / "a.txt").write_text("x")
    asked = []

    async def approve(name, args, decision):
        asked.append(name)
        return False  # the owner says no

    replies = [call("move_path", source="Desktop/a.txt", destination="Documents"),
               call("delete_path", path="Desktop/a.txt"), say("ok")]  # fmt: skip
    vault_, chat, agent = agent_with(tmp_path, key, world, replies, Mode("ask"), approve)
    await agent.turn(Session(), "move it then delete it")
    assert asked == ["move_path", "delete_path"] and (home / "Desktop" / "a.txt").exists()
    vault_.close()


async def test_edits_mode_moves_without_asking_but_still_asks_to_delete(tmp_path, key, world):  # noqa: F811
    home, _ = world
    (home / "Desktop" / "a.txt").write_text("x")
    asked = []

    async def approve(name, args, decision):
        asked.append(name)
        return False

    bin_ = Bin()
    replies = [call("move_path", source="Desktop/a.txt", destination="Documents"),
               call("delete_path", path="Documents/a.txt"), say("ok")]  # fmt: skip
    vault_, chat, agent = agent_with(tmp_path, key, world, replies, Mode("edits"), approve, bin_)
    await agent.turn(Session(), "move it then delete it")
    assert asked == ["delete_path"] and (home / "Documents" / "a.txt").exists() and bin_.sent == []
    vault_.close()


async def test_full_mode_asks_nothing_but_the_hard_limits_hold(tmp_path, key, world):  # noqa: F811
    home, vault = world
    (home / "Desktop" / "a.txt").write_text("x")
    asked = []

    async def approve(name, args, decision):
        asked.append(name)
        return True

    bin_ = Bin()
    replies = [call("delete_path", path="Desktop/a.txt"), call("delete_path", path=str(vault)),
               call("delete_path", path="Desktop"), say("done")]  # fmt: skip
    vault_, chat, agent = agent_with(tmp_path, key, world, replies, Mode("full"), approve, bin_)
    await agent.turn(Session(), "delete a.txt, the vault, and the desktop")
    assert asked == [] and bin_.sent == [home / "Desktop" / "a.txt"]
    assert (vault / "vault.db").exists() and (home / "Desktop").is_dir()
    vault_.close()


async def test_switching_the_mode_while_chatting_changes_what_is_asked(tmp_path, key, world):  # noqa: F811
    home, _ = world
    (home / "Desktop" / "a.txt").write_text("x")
    (home / "Desktop" / "b.txt").write_text("y")
    asked = []

    async def approve(name, args, decision):
        asked.append(name)
        return True

    mode = Mode("ask")
    # single-step requests end on the tool's own sentence, so one reply per turn
    replies = [
        call("move_path", source="Desktop/a.txt", destination="Documents"),
        call("move_path", source="Desktop/b.txt", destination="Documents"),
    ]
    vault_, chat, agent = agent_with(tmp_path, key, world, replies, mode, approve)
    await agent.turn(Session(), "move a.txt")
    mode.set("edits")
    agent.profile_name = mode.profile  # what /mode does
    await agent.turn(Session(), "move b.txt")
    assert asked == ["move_path"]  # asked the first time only
    assert (home / "Documents" / "a.txt").exists() and (home / "Documents" / "b.txt").exists()
    vault_.close()


# ---- open_path and finding a file by name wherever it is -----------------------------------------


class Opened:
    def __init__(self):
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> None:
        self.paths.append(path)


def tools_with_opener(world, opener):
    home, vault = world
    roots = [home / "Desktop", home / "Documents", home / "Downloads"]
    tools = file_op_tools(roots, home, None, Bin(), data_dirs=[vault], opener=opener)
    return {t.name: t for t in tools}


async def test_open_a_file_or_a_folder_the_way_a_double_click_does(world):
    home, _ = world
    (home / "Documents" / "talk.pptx").write_bytes(b"deck")
    opener = Opened()
    tools = tools_with_opener(world, opener)
    out = await use(tools, "open_path", path="Documents/talk.pptx")
    await use(tools, "open_path", path="Downloads")
    assert out == "Opened talk.pptx" or getattr(out, "text", out) == "Opened talk.pptx"
    assert opener.paths == [home / "Documents" / "talk.pptx", home / "Downloads"]


async def test_a_file_is_found_by_name_even_inside_a_subfolder(world):
    home, _ = world
    deep = home / "Documents" / "Ragnarok"
    deep.mkdir(parents=True)
    (deep / "Climate change.pptx").write_bytes(b"deck")
    opener = Opened()
    await use(tools_with_opener(world, opener), "open_path", path="Climate change.pptx")
    assert opener.paths == [deep / "Climate change.pptx"]


async def test_two_files_with_the_same_name_are_listed_not_guessed(world):
    home, _ = world
    for folder in ("Desktop", "Documents"):  # the same name at the top of two folders
        (home / folder / "plan.docx").write_bytes(b"x")
    opener = Opened()
    out = await use(tools_with_opener(world, opener), "open_path", path="plan.docx")
    assert out.startswith("error:") and "more than one place" in out and opener.paths == []


async def test_a_file_at_the_top_of_a_folder_wins_over_one_buried_deeper(world):
    home, _ = world
    (home / "Documents" / "Old").mkdir()
    (home / "Documents" / "Old" / "plan.docx").write_bytes(b"old")
    (home / "Desktop" / "plan.docx").write_bytes(b"new")
    opener = Opened()
    await use(tools_with_opener(world, opener), "open_path", path="plan.docx")
    assert opener.paths == [home / "Desktop" / "plan.docx"]


@pytest.mark.parametrize(
    "name", ["setup.exe", "run.bat", "x.ps1", "m.msi", "s.vbs", "a.lnk", "j.jar", "r.reg"]
)
async def test_programs_and_scripts_are_never_opened(world, name):
    home, _ = world
    (home / "Downloads" / name).write_bytes(b"x")
    opener = Opened()
    out = await use(tools_with_opener(world, opener), "open_path", path=f"Downloads/{name}")
    assert out.startswith("error:") and "never start" in out and opener.paths == []


async def test_open_respects_the_guard(world):
    home, vault = world
    opener = Opened()
    tools = tools_with_opener(world, opener)
    for bad in (str(vault), ".ssh", "C:/Windows/notepad.exe", "Desktop/missing.pdf"):
        assert (await use(tools, "open_path", path=bad)).startswith("error:"), bad
    assert opener.paths == []


async def test_a_stand_in_user_name_and_a_bare_name_both_work_for_every_source_tool(world):
    home, _ = world
    (home / "Downloads" / "r.pdf").write_bytes(make_pdf(PDF_PAGES))
    tools = tools_for(world)
    await use(tools, "extract_text", source=r"C:\Users\name\Downloads\r.pdf")
    assert (home / "Downloads" / "r.txt").exists()
    await use(tools, "extract_text", source="r.pdf", destination="Desktop/again.txt")
    assert (home / "Desktop" / "again.txt").exists()
    (home / "Desktop" / "old.txt").write_text("x")
    bin_ = Bin()
    await use(tools_for(world, recycler=bin_), "delete_path", path="old.txt")
    assert bin_.sent == [home / "Desktop" / "old.txt"]


async def test_rename_in_place_and_copy_into_a_folder_by_bare_names(world):
    home, _ = world
    (home / "Desktop" / "notes.txt").write_text("x")
    await use(tools_for(world), "move_path", source="notes.txt", destination="ideas.txt")
    assert (home / "Desktop" / "ideas.txt").exists() and not (
        home / "Desktop" / "notes.txt"
    ).exists()
    (home / "Documents" / "Reports").mkdir()
    await use(tools_for(world), "copy_path", source="ideas.txt", destination="Reports")
    assert (home / "Documents" / "Reports" / "ideas.txt").exists()


async def test_a_new_file_or_folder_with_a_bare_name_lands_in_documents_ragnarok(world):
    home, _ = world
    await use(tools_for(world), "create_file", path="addition.py", content="print(1+2)")
    await use(tools_for(world), "make_folder", path="Projects")
    assert (home / "Documents" / "Ragnarok" / "addition.py").exists()
    assert (home / "Documents" / "Ragnarok" / "Projects").is_dir()
    assert not (home / "addition.py").exists()  # not left loose in the profile folder


# ---- names said loosely: "deep learning" finds Deep_learning --------------------------


async def test_a_folder_is_opened_by_what_the_user_says_not_its_exact_spelling(world):
    home, _ = world
    (home / "Desktop" / "Deep_learning").mkdir()
    (home / "Desktop" / "Deep_learning" / "Deep Learning.pdf").write_bytes(
        b"x"
    )  # deeper, same words
    opener = Opened()
    await use(tools_with_opener(world, opener), "open_path", path="deep learning")
    assert opener.paths == [home / "Desktop" / "Deep_learning"]  # the folder, not the file in it


async def test_a_deck_is_found_from_a_few_words_of_its_name(world):
    home, _ = world
    (home / "Documents" / "Ragnarok").mkdir()
    (home / "Documents" / "Ragnarok" / "Climate change.pptx").write_bytes(b"deck")
    opener = Opened()
    await use(tools_with_opener(world, opener), "open_path", path="climate ppt")
    assert opener.paths == [home / "Documents" / "Ragnarok" / "Climate change.pptx"]


async def test_loose_names_that_fit_several_things_are_listed_not_guessed(world):
    home, _ = world
    (home / "Documents" / "Q1 sales report.docx").write_bytes(b"1")
    (home / "Desktop" / "Q2 sales report.docx").write_bytes(b"2")
    opener = Opened()
    out = await use(tools_with_opener(world, opener), "open_path", path="sales report")
    assert out.startswith("error:") and "several things" in out and opener.paths == []


async def test_a_name_that_matches_nothing_is_just_not_found(world):
    out = await use(tools_with_opener(world, Opened()), "open_path", path="no such thing")
    assert out.startswith("error:")
