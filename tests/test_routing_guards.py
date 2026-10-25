"""Guards that steer a small model away from the wrong tool, found by the real-model battery."""

import pytest

from ragnarok.runtime.tools import CURRENT_TEXT
from ragnarok.skills.documents import CreatePresentationArgs, document_tools
from ragnarok.skills.downloads import DownloadError, resolve_source
from ragnarok.skills.steps import FINISHED_NOTE, reply


@pytest.mark.parametrize(
    "name", ["interview-report.pdf", "Lab Report.docx", "notes.TXT", "slides.pptx", "data.csv"]
)
def test_a_file_name_is_not_downloaded_it_is_read(name):
    with pytest.raises(DownloadError) as caught:
        resolve_source(name, f"summarize {name} in my downloads")
    message = str(caught.value)
    assert "file name" in message and "find_files" in message and "read_file" in message


def test_real_links_and_program_names_still_work():
    assert resolve_source("steam", "download steam")[1]
    link = "https://example.com/files/report.pdf"
    assert resolve_source(link, f"get {link}")[0] == link
    assert resolve_source("example.com/files/report.pdf", f"get {link}")[0] == link
    with pytest.raises(DownloadError, match="no trusted link"):
        resolve_source("photoshop plugin", "download photoshop plugin")  # not a file name


async def test_an_outline_with_everything_on_one_slide_is_sent_back(tmp_path):
    tool = next(t for t in document_tools(tmp_path) if t.name == "create_presentation")
    one_slide = "# Climate Change\n" + "\n".join(f"- point number {i}" for i in range(7))
    result = await tool.handler(CreatePresentationArgs(title="Climate", outline=one_slide))
    text = result if isinstance(result, str) else result.text
    assert text.startswith("error:") and "its own '# Slide title'" in text
    assert not list((tmp_path / "Ragnarok").glob("*.pptx"))  # nothing half-made was saved


async def test_a_short_single_slide_is_still_allowed(tmp_path):
    tool = next(t for t in document_tools(tmp_path) if t.name == "create_presentation")
    result = await tool.handler(
        CreatePresentationArgs(title="Tiny", outline="# Only slide\n- one\n- two")
    )
    assert "Created" in (result if isinstance(result, str) else result.text)


def test_a_result_that_leaves_the_turn_open_tells_the_model_it_is_finished():
    CURRENT_TEXT.set("make the report and then open it")
    open_ended = reply("Created Report.pdf in Documents.")
    assert isinstance(open_ended, str) and open_ended.endswith(FINISHED_NOTE)
    assert "do not list, open or re-check" in open_ended
    CURRENT_TEXT.set("make the report")
    final = reply("Created Report.pdf in Documents.")
    assert final.final and final.text == "Created Report.pdf in Documents."  # no note on the answer
