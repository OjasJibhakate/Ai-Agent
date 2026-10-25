"""A small PDF writer (no dependency): a title, headings, bullets and paragraphs on A4 pages.

It uses the two built-in PDF fonts (Helvetica and Helvetica-Bold), so nothing is embedded and every
viewer shows it the same. Text is encoded as Windows-1252; characters outside it become "?".
The same input always gives the same bytes (no dates, no random ids), so asking twice for the same
document does not make a second copy.
"""

from __future__ import annotations

PAGE_W, PAGE_H = 595, 842  # A4 in points
MARGIN = 56
BODY, HEAD, TITLE = 11, 15, 22
LEADING = 1.4
BULLET_INDENT = 16

# Helvetica advance widths (per 1000 em) for ASCII 32..126; anything else is measured as 556.
_W = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]  # fmt: skip


def _width(text: str, size: float, bold: bool) -> float:
    total = sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text)
    return total * size / 1000 * (1.08 if bold else 1.0)  # bold is a little wider: stay safe


def _wrap(text: str, size: float, bold: bool, room: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        while _width(word, size, bold) > room and len(word) > 1:  # a word longer than the line
            cut = len(word) - 1
            while cut > 1 and _width(word[:cut], size, bold) > room:
                cut -= 1
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:cut])
            word = word[cut:]
        candidate = f"{current} {word}".strip()
        if current and _width(candidate, size, bold) > room:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _blocks(title: str, body: str) -> list[tuple[str, str, float]]:
    """(font, text, indent) for every line to print, with spacing as blank entries."""
    out: list[tuple[str, str, float]] = [("title", title, 0)]
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append(("gap", "", 0))
        elif line.startswith("# "):
            out.append(("head", line[2:].strip(), 0))
        elif line.startswith(("- ", "* ")):
            out.append(("bullet", line[2:].strip(), BULLET_INDENT))
        else:
            out.append(("body", line.strip(), 0))
    return out


def build_pdf(title: str, body: str) -> bytes:
    """A valid PDF. Lines starting "# " are headings and "- " are bullets; the rest is text."""
    room = PAGE_W - 2 * MARGIN
    pages: list[list[bytes]] = [[]]
    y = PAGE_H - MARGIN

    def put(font: str, size: float, x: float, text: str) -> None:
        pages[-1].append(
            b"BT /%s %.1f Tf %.1f %.1f Td (%s) Tj ET" % (font.encode(), size, x, y, _escape(text))
        )

    def advance(size: float, before: float = 0) -> None:
        nonlocal y
        y -= before + size * LEADING

    def room_for(lines: int, size: float) -> None:
        """Start a new page if `lines` more lines would run into the footer."""
        nonlocal y
        if y - (lines - 1) * size * LEADING < MARGIN + 20:
            pages.append([])
            y = PAGE_H - MARGIN - size

    y -= TITLE  # the first line sits one line below the top margin
    for kind, text, indent in _blocks(title, body):
        if kind == "gap":
            y -= BODY * 0.6
            continue
        size, bold, font, before = {
            "title": (TITLE, True, "F2", 0),
            "head": (HEAD, True, "F2", 10),
            "bullet": (BODY, False, "F1", 2),
            "body": (BODY, False, "F1", 2),
        }[kind]
        lines = _wrap(text, size, bold, room - indent - (BULLET_INDENT if kind == "bullet" else 0))
        if kind == "head" and y - before - size * LEADING * 3 < MARGIN + 20:
            pages.append([])  # a heading never sits alone at the foot of a page
            y = PAGE_H - MARGIN - size * LEADING
            before = 0
        room_for(3 if kind == "head" else 1, size)  # a heading never sits alone at a page foot
        for i, line in enumerate(lines):
            room_for(1, size)
            if i == 0 and kind == "bullet":
                put("F1", size, MARGIN + 2, "•")  # encoded as the Windows-1252 bullet
            put(font, size, MARGIN + indent, line)
            advance(size, before if i == 0 else 0)
        if kind == "title":
            y -= 8

    # footer with page numbers, drawn after the layout is known
    for number, commands in enumerate(pages, start=1):
        label = f"{number} / {len(pages)}"
        x = (PAGE_W - _width(label, 9, False)) / 2
        commands.append(b"BT /F1 9 Tf %.1f %d Td (%s) Tj ET" % (x, MARGIN // 2, _escape(label)))

    objects: list[bytes] = []

    def add(data: bytes) -> int:
        objects.append(data)
        return len(objects)

    catalog = add(b"")  # filled in below, once the page tree exists
    tree = add(b"")
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    info = add(b"<< /Title (%s) /Producer (Ragnarok) >>" % _escape(title[:120]))
    kids: list[int] = []
    for commands in pages:
        stream = b"\n".join(commands)
        content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] "
                b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>"
                % (tree, PAGE_W, PAGE_H, f1, f2, content)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree
    refs = b" ".join(b"%d 0 R" % k for k in kids)
    objects[tree - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (refs, len(kids))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, data in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, data)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        info,
        xref,
    )
    return bytes(out)
