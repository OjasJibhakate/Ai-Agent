"""Skill: create a new document (Word .docx, Markdown or plain text) in Documents\\Ragnarok.

The model writes the text; this code makes the file. It only ever creates NEW files, in one fixed
folder, under a cleaned-up name: it cannot overwrite or edit anything that already exists, and it
cannot write anywhere else. Asking for the same document twice does not make a second copy.

The .docx is built by hand (a zip of three small XML parts) so no new dependency is needed. Word,
and anything else that reads .docx, opens it as a plain document: a title, paragraphs and bullets.
"""

from __future__ import annotations

import asyncio
import os
import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

from pydantic import Field

from ragnarok.policy.permissions import Risk
from ragnarok.runtime.tools import Tool, ToolArgs, ToolResult
from ragnarok.skills.folders import documents_folder

MAX_CHARS = 20_000
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # characters XML 1.0 cannot hold
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PKG = "http://schemas.openxmlformats.org/package/2006"
_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOC_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_REL_TYPE = "application/vnd.openxmlformats-package.relationships+xml"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_PARTS = {
    "[Content_Types].xml": (
        f'{_HEAD}<Types xmlns="{_PKG}/content-types">'
        f'<Default Extension="rels" ContentType="{_REL_TYPE}"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{_DOC_TYPE}"/></Types>'
    ),
    "_rels/.rels": (
        f'{_HEAD}<Relationships xmlns="{_PKG}/relationships">'
        f'<Relationship Id="rId1" Type="{_OFFICE}/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    ),
}


def _paragraph(text: str, *, bold: bool = False, size: int = 0) -> str:
    props = ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "")
    run_props = f"<w:rPr>{props}</w:rPr>" if props else ""
    text = escape(_XML_BAD.sub("", text))
    if not text:
        return "<w:p/>"
    return f'<w:p><w:r>{run_props}<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def build_docx(title: str, body: str) -> bytes:
    """A minimal, valid Word document. The same input always gives the same bytes."""
    paragraphs = [_paragraph(title, bold=True, size=40)]
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.startswith(("- ", "* ")):
            paragraphs.append(_paragraph("• " + line[2:].strip()))
        elif line.startswith("# "):
            paragraphs.append(_paragraph(line[2:].strip(), bold=True, size=30))
        else:
            paragraphs.append(_paragraph(line))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{"".join(paragraphs)}<w:sectPr/></w:body></w:document>'
    )
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in {**_PARTS, "word/document.xml": document}.items():
            info = zipfile.ZipInfo(
                name, date_time=(2020, 1, 1, 0, 0, 0)
            )  # fixed: same bytes each time
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, xml.encode("utf-8"))
    return out.getvalue()


def build_text(title: str, body: str, fmt: str) -> bytes:
    head = f"# {title}\n\n" if fmt == "md" else f"{title}\n{'=' * min(len(title), 60)}\n\n"
    return (head + body.strip("\n") + "\n").encode("utf-8")


def safe_stem(text: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    stem = re.sub(r"\s+", " ", stem).strip(". ")[:80].strip(". ") or "Document"
    return f"_{stem}" if stem.lower() in RESERVED else stem


def target_for(folder: Path, stem: str, ext: str, data: bytes) -> tuple[Path, bool]:
    """(where to write, True if that exact document is already there)."""
    path = folder / f"{stem}.{ext}"
    for n in range(1, 1000):
        if not path.exists():
            return path, False
        if path.read_bytes() == data:
            return path, True
        path = folder / f"{stem} ({n}).{ext}"
    raise OSError("too many documents with that name")


class CreateDocumentArgs(ToolArgs):
    title: str = Field(min_length=2, max_length=120, description="The document's title.")
    content: str = Field(
        min_length=1,
        max_length=MAX_CHARS,
        description="The text, one item per line; start list items with '- '. For a list of "
        "files, copy the exact names you found.",
    )
    format: Literal["docx", "md", "txt"] = Field(
        default="docx",
        description="Always docx (a Word document) unless markdown or a text file was asked for.",
    )


def _write(path: Path, data: bytes) -> None:
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)  # a crash leaves a .part file, never half a document


def document_tools(documents: Path | None = None) -> list[Tool]:
    folder = (documents or documents_folder()) / "Ragnarok"

    async def create_document(args: CreateDocumentArgs) -> str | ToolResult:
        if args.format == "docx":
            data = build_docx(args.title, args.content)
        else:
            data = build_text(args.title, args.content, args.format)
        try:
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
            for stale in await asyncio.to_thread(lambda: list(folder.glob("*.part"))):
                await asyncio.to_thread(stale.unlink, missing_ok=True)  # left by an earlier crash
            path, already = await asyncio.to_thread(
                target_for, folder, safe_stem(args.title), args.format, data
            )
            if not already:
                await asyncio.to_thread(_write, path, data)
        except OSError as exc:
            return f"error: I couldn't save the document ({type(exc).__name__})."
        verb = "Already created" if already else "Created"
        return ToolResult(f"{verb} {path.name} in {folder}.", final=True)

    return [
        Tool(
            name="create_document",
            description="Create a new document (Word, Markdown or text) with the given title and "
            "text in the user's Documents folder. Use it when the user asks you to make a doc, "
            "note or file; it cannot change existing files.",
            args_model=CreateDocumentArgs,
            risk=Risk.R1,  # a new file only; deleting it undoes it
            idempotent=True,  # the same document is recognised and not written twice
            timeout_s=20,
            sandbox="workspace-write",
            fs={"write": [str(folder)]},
            handler=create_document,
        )
    ]
