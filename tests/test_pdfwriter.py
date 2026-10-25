"""The PDF writer: valid files, real text, page breaks, and the same bytes every time."""

import io

import pytest
from pypdf import PdfReader

from ragnarok.skills.pdfwriter import _width, _wrap, build_pdf


def read(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data), strict=True)  # strict: a broken xref or stream is an error


def text_of_pages(data: bytes) -> list[str]:
    return [page.extract_text() or "" for page in read(data).pages]


def test_a_small_document_reads_back_with_title_heading_bullets_and_text():
    data = build_pdf(
        "Quarterly Report", "Intro line.\n\n# Results\n- first point\n- second point\n"
    )
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    pages = text_of_pages(data)
    assert len(pages) == 1
    text = pages[0]
    for expected in ("Quarterly Report", "Intro line.", "Results", "first point", "second point"):
        assert expected in text
    assert text.index("Quarterly Report") < text.index("Results") < text.index("second point")
    assert "1 / 1" in text  # a page number in the footer


def test_special_characters_survive():
    body = r"Brackets (like these) and a back\slash, café, naïve, 5 € and “quotes” – dash."
    text = text_of_pages(build_pdf("Odd (title) \\ here", body))[0]
    assert "(like these)" in text and "back\\slash" in text
    assert "café" in text and "naïve" in text and "€" in text and "“quotes”" in text


def test_characters_outside_windows_1252_become_question_marks_not_errors():
    text = text_of_pages(build_pdf("Maths", "Sum of ∑ and 日本"))[0]
    assert "Sum of ? and" in text


def test_long_text_wraps_inside_the_margins_and_flows_onto_more_pages():
    bullets = "\n".join(f"- item number {i} " + "word " * 30 for i in range(80))
    data = build_pdf("Long", "# A section\n" + bullets)
    reader = read(data)
    assert len(reader.pages) >= 3
    texts = text_of_pages(data)
    assert all(t.strip() for t in texts)  # no blank pages, including the last one
    assert "item number 79" in texts[-1]
    assert f"{len(texts)} / {len(texts)}" in texts[-1]
    for page in reader.pages:  # nothing is drawn off the right edge
        assert float(page.mediabox.width) == 595


def test_a_word_longer_than_a_line_is_broken_not_clipped():
    lines = _wrap("x" * 400, 11, False, 480)
    assert len(lines) > 1 and "".join(lines) == "x" * 400
    assert all(_width(line, 11, False) <= 480 for line in lines)


def test_a_heading_is_not_left_alone_at_the_bottom_of_a_page():
    filler = "\n".join(f"- line {i}" for i in range(46))  # fills most of page one
    texts = text_of_pages(build_pdf("T", filler + "\n# Lonely heading\n- under it"))
    for page_number, text in enumerate(texts):
        if "Lonely heading" in text and page_number < len(texts) - 1:
            assert "under it" in text  # it moved to the next page together with its first line


def test_the_same_input_gives_the_same_bytes():
    assert build_pdf("A", "# B\n- c") == build_pdf("A", "# B\n- c")
    assert build_pdf("A", "# B\n- c") != build_pdf("A", "# B\n- d")


def test_empty_body_is_still_a_valid_pdf():
    assert len(read(build_pdf("Only a title", "")).pages) == 1


@pytest.mark.parametrize("n", [1, 50, 200])
def test_page_count_and_footer_agree(n):
    data = build_pdf("Counts", "\n".join(f"- row {i}" for i in range(n)))
    texts = text_of_pages(data)
    assert f"{len(texts)} / {len(texts)}" in texts[-1]
