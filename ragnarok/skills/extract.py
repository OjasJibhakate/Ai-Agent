"""Turn a document into plain text: PDF, Word (.docx), PowerPoint (.pptx) or any text file.

Documents come from anywhere, so they are treated as untrusted: size, page and output limits, no
macros or embedded files are touched, and only the visible text is returned. This does real work
(parsing), so callers in async code run it with asyncio.to_thread.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 100 * 1024 * 1024  # a document larger than this is not read
MAX_PAGES = 1500
MAX_TEXT_CHARS = 5_000_000
MAX_XML_BYTES = 60 * 1024 * 1024  # inside a .docx/.pptx (a zip can lie about its size)
DOCUMENT_EXT = {".pdf", ".docx", ".pptx"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class ExtractError(ValueError):
    """The document can't be turned into text (the message is shown to the user)."""


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractError("that PDF is password-protected, so I can't read it")
        pages = len(reader.pages)
        if pages > MAX_PAGES:
            raise ExtractError(f"that PDF has {pages} pages; my limit is {MAX_PAGES}")
        out: list[str] = []
        size = 0
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            out.append(f"--- page {number} ---\n{text}")
            size += len(text)
            if size > MAX_TEXT_CHARS:
                raise ExtractError("that PDF holds more text than I will extract")
    except ExtractError:
        raise
    except (PyPdfError, OSError, ValueError, KeyError, RecursionError, NotImplementedError) as exc:
        raise ExtractError(f"I couldn't read that PDF ({type(exc).__name__})") from exc
    if size == 0:
        raise ExtractError(
            "that PDF has no text I can extract: it is probably scanned pages (pictures of text)"
        )
    return "\n\n".join(out)


def _xml_from_zip(archive: zipfile.ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ExtractError("that document is too large to read")
    return ET.fromstring(archive.read(name))


def _paragraph(node: ET.Element, text_tag: str, tab: str, br: str) -> str:
    parts: list[str] = []
    for el in node.iter():
        if el.tag == text_tag:
            parts.append(el.text or "")
        elif el.tag == tab:
            parts.append("\t")
        elif el.tag == br:
            parts.append("\n")
    return "".join(parts)


def docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            body = _xml_from_zip(z, "word/document.xml").find(f"{_W}body")
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise ExtractError(f"I couldn't read that Word file ({type(exc).__name__})") from exc
    if body is None:
        raise ExtractError("that Word file has no body")
    lines: list[str] = []
    for block in body:
        if block.tag == f"{_W}p":
            lines.append(_paragraph(block, f"{_W}t", f"{_W}tab", f"{_W}br"))
        elif block.tag == f"{_W}tbl":
            for row in block.iter(f"{_W}tr"):
                cells = [
                    " ".join(
                        _paragraph(p, f"{_W}t", f"{_W}tab", f"{_W}br") for p in cell.iter(f"{_W}p")
                    )
                    for cell in row.findall(f"{_W}tc")
                ]
                lines.append(" | ".join(c.strip() for c in cells))
    text = "\n".join(lines)
    if len(text) > MAX_TEXT_CHARS:
        raise ExtractError("that document holds more text than I will extract")
    return text


def pptx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(
                (n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)),  # type: ignore[union-attr]
            )
            slides = []
            for number, name in enumerate(names, start=1):
                root = _xml_from_zip(z, name)
                paragraphs = [
                    _paragraph(p, f"{_A}t", f"{_A}tab", f"{_A}br") for p in root.iter(f"{_A}p")
                ]
                slides.append(f"--- slide {number} ---\n" + "\n".join(p for p in paragraphs if p))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise ExtractError(f"I couldn't read that PowerPoint file ({type(exc).__name__})") from exc
    return "\n\n".join(slides)


def text_of(path: Path) -> str:
    """The text of a PDF, Word, PowerPoint or plain-text file."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ExtractError("I couldn't open that file") from exc
    if size > MAX_FILE_BYTES:
        raise ExtractError("that file is too large to read")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf_text(path)
    if suffix == ".docx":
        return docx_text(path)
    if suffix == ".pptx":
        return pptx_text(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ExtractError("I couldn't read that file") from exc
    if b"\x00" in raw[:2048]:
        raise ExtractError("that looks like a binary file, not text")
    return raw.decode("utf-8", errors="replace")
