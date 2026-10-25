"""Tiny real documents for tests: a PDF with a text layer, a PowerPoint, and an image-only PDF."""

from __future__ import annotations

import io
import zipfile


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], *, scanned: bool = False) -> bytes:
    """A valid PDF: one page per list of lines, in the built-in Helvetica font.

    `scanned=True` makes pages with a drawn rectangle and no text, like a scan.
    """
    objects: list[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1"))
        return len(objects)

    catalog = add("<< /Type /Catalog /Pages 2 0 R >>")  # object 1
    pages_obj = add("")  # object 2, filled in below
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")  # object 3
    kids = []
    for lines in pages:
        if scanned:
            stream = "0.5 g 50 50 400 600 re f"
        else:
            body = "\n".join(f"({_esc(line)}) Tj T*" for line in lines)
            stream = f"BT /F1 12 Tf 14 TL 72 720 Td\n{body}\nET"
        content = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        page = add(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>"
        )
        kids.append(f"{page} 0 R")
    objects[pages_obj - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode("latin-1")
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    size = len(objects) + 1
    trailer = f"trailer\n<< /Size {size} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    out.write(trailer.encode())
    return out.getvalue()


def make_pptx(slides: list[list[str]]) -> bytes:
    """A bare-bones .pptx: each slide is a list of paragraphs."""
    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for number, paragraphs in enumerate(slides, start=1):
            body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paragraphs)
            z.writestr(
                f"ppt/slides/slide{number}.xml",
                f'<p:sld xmlns:a="{ns}" xmlns:p="urn:p"><p:cSld>{body}</p:cSld></p:sld>',
            )
    return out.getvalue()
