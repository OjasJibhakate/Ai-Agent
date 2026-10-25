"""Build PowerPoint (.pptx) and Excel (.xlsx) files by hand, with no extra dependency.

Both formats are a zip of small XML parts. The builders here take plain text from the model (an
outline, a table) and produce a clean, valid file: the same input always gives the same bytes, all
text is XML-escaped, and nothing in the file can run anything (no macros, links or embedded files;
spreadsheet formulas are limited to ordinary arithmetic and functions).

Outline format for a presentation ("# " starts a slide, "- " is a bullet):

    # Why the climate is changing
    - Greenhouse gases trap heat
    - Burning fossil fuels adds CO2
    # What we can do
    - Use clean energy

Table format for a spreadsheet: one row per line, cells separated by | (or commas or tabs); the
first row is the header; numbers become numbers; a cell starting with = is a formula.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

MAX_SLIDES = 40
MAX_BULLETS = 12
MAX_ROWS = 2000
MAX_COLUMNS = 50
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_PKG = "http://schemas.openxmlformats.org/package/2006"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
NAVY, TEAL, INK, MUTED = "1B2A41", "3AAFA9", "222222", "5A6472"


def _x(text: str) -> str:
    return escape(_XML_BAD.sub("", text))


def _zip(parts: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in parts.items():
            info = zipfile.ZipInfo(
                name, date_time=(2020, 1, 1, 0, 0, 0)
            )  # fixed: same bytes always
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, xml.encode("utf-8"))
    return out.getvalue()


def _rels(*items: tuple[str, str, str]) -> str:
    body = "".join(
        f'<Relationship Id="{i}" Type="{_R}/{kind}" Target="{target}"/>'
        for i, kind, target in items
    )
    return f'{_HEAD}<Relationships xmlns="{_PKG}/relationships">{body}</Relationships>'


# ---- PowerPoint -------------------------------------------------------------------------------


@dataclass
class SlideSpec:
    title: str
    bullets: list[str] = field(default_factory=list)


def parse_outline(outline: str) -> list[SlideSpec]:
    """Slides from the outline text. Text before the first "# " becomes bullets of a first slide."""
    slides: list[SlideSpec] = []
    for raw in outline.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            slides.append(SlideSpec(line.lstrip("#").strip() or "Untitled"))
        else:
            if not slides:
                slides.append(SlideSpec("Overview"))
            bullet = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line)
            if bullet:
                slides[-1].bullets.append(bullet)
    if not slides:
        raise ValueError("the outline is empty: give lines like '# Slide title' and '- a point'")
    if len(slides) > MAX_SLIDES:
        raise ValueError(f"at most {MAX_SLIDES} slides")
    for s in slides:
        if len(s.bullets) > MAX_BULLETS:
            raise ValueError(f"at most {MAX_BULLETS} points on a slide ({s.title!r} has more)")
    return slides


def _font_size(bullets: list[str]) -> int:
    chars = sum(len(b) for b in bullets)
    if len(bullets) > 8 or chars > 700:
        return 1600
    if len(bullets) > 6 or chars > 450:
        return 1800
    if len(bullets) > 4 or chars > 300:
        return 2000
    return 2400


def _shape(sid: int, name: str, x: int, y: int, w: int, h: int, *, fill: str, ph: str = "") -> str:
    placeholder = f"<p:nvPr>{ph}</p:nvPr>" if ph else "<p:nvPr/>"
    locks = '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>' if ph else "<p:cNvSpPr/>"
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/>{locks}{placeholder}</p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="{fill}"/>'
        "</a:solidFill><a:ln><a:noFill/></a:ln></p:spPr></p:sp>"
    )


def _text_shape(
    sid: int,
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    paragraphs: str,
    *,
    ph: str = "",
    anchor: str = "t",
) -> str:
    placeholder = f"<p:nvPr>{ph}</p:nvPr>" if ph else "<p:nvPr/>"
    locks = '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>' if ph else '<p:cNvSpPr txBox="1"/>'
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/>{locks}{placeholder}</p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" anchor="{anchor}"><a:normAutofit/></a:bodyPr><a:lstStyle/>'
        f"{paragraphs}</p:txBody></p:sp>"
    )


def _run(text: str, size: int, color: str, *, bold: bool = False) -> str:
    return (
        f'<a:r><a:rPr lang="en-US" sz="{size}" b="{int(bold)}" dirty="0">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:rPr><a:t>{_x(text)}</a:t></a:r>'
    )


def _slide_xml(index: int, spec: SlideSpec, deck_title: str, subtitle: str) -> str:
    first = index == 0
    tree = (
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
    )
    background = ""
    if first:
        background = (
            f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{NAVY}"/></a:solidFill><a:effectLst/>'
            "</p:bgPr></p:bg>"
        )
        tree += _shape(2, "Accent", 685800, 3200400, 1828800, 54864, fill=TEAL)
        tree += _text_shape(
            3,
            "Title",
            685800,
            1371600,
            10820400,
            1737360,
            f'<a:p><a:pPr algn="l"/>{_run(deck_title, 4400, "FFFFFF", bold=True)}</a:p>',
            ph='<p:ph type="title"/>',
            anchor="b",
        )
        if subtitle:
            tree += _text_shape(
                4,
                "Subtitle",
                685800,
                3429000,
                10820400,
                914400,
                f'<a:p><a:pPr algn="l"/>{_run(subtitle, 2400, "C9D3E0")}</a:p>',
            )
        if spec.bullets:  # the outline gave the opening slide points of its own
            body = "".join(
                f'<a:p><a:pPr algn="l"/>{_run(b, 2000, "C9D3E0")}</a:p>' for b in spec.bullets[:4]
            )
            tree += _text_shape(5, "Notes", 685800, 4500000, 10820400, 1600000, body)
    else:
        tree += _shape(2, "Accent", 0, 0, 228600, 6858000, fill=TEAL)
        tree += _text_shape(
            3,
            "Title",
            685800,
            365760,
            10820400,
            1005840,
            f'<a:p><a:pPr algn="l"/>{_run(spec.title, 3600, NAVY, bold=True)}</a:p>',
            ph='<p:ph type="title"/>',
            anchor="ctr",
        )
        tree += _shape(4, "Rule", 685800, 1417320, 1371600, 36576, fill=TEAL)
        size = _font_size(spec.bullets)
        body = (
            "".join(
                '<a:p><a:pPr marL="342900" indent="-342900"><a:spcBef><a:spcPts val="900"/></a:spcBef>'
                f'<a:buClr><a:srgbClr val="{TEAL}"/></a:buClr><a:buFont typeface="Arial"/>'
                f'<a:buChar char="&#8226;"/></a:pPr>{_run(b, size, INK)}</a:p>'
                for b in spec.bullets
            )
            or '<a:p><a:endParaRPr lang="en-US"/></a:p>'
        )
        tree += _text_shape(
            5, "Content", 685800, 1645920, 10820400, 4572000, body, ph='<p:ph idx="1"/>'
        )
    return (
        f'{_HEAD}<p:sld xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"><p:cSld>{background}'
        f"<p:spTree>{tree}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _theme_xml() -> str:
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = lambda w: f'<a:ln w="{w}"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'  # noqa: E731
    font = '<a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/>'
    return (
        f'{_HEAD}<a:theme xmlns:a="{_A}" name="Ragnarok"><a:themeElements>'
        '<a:clrScheme name="Ragnarok">'
        f'<a:dk1><a:srgbClr val="{INK}"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
        f'<a:dk2><a:srgbClr val="{NAVY}"/></a:dk2><a:lt2><a:srgbClr val="F2F4F7"/></a:lt2>'
        f'<a:accent1><a:srgbClr val="{TEAL}"/></a:accent1><a:accent2><a:srgbClr val="{NAVY}"/></a:accent2>'
        '<a:accent3><a:srgbClr val="E07A5F"/></a:accent3><a:accent4><a:srgbClr val="F2CC8F"/></a:accent4>'
        '<a:accent5><a:srgbClr val="81B29A"/></a:accent5><a:accent6><a:srgbClr val="6C757D"/></a:accent6>'
        '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
        f'</a:clrScheme><a:fontScheme name="Ragnarok"><a:majorFont>{font}</a:majorFont>'
        f'<a:minorFont>{font}</a:minorFont></a:fontScheme><a:fmtScheme name="Ragnarok">'
        f"<a:fillStyleLst>{fill}{fill}{fill}</a:fillStyleLst>"
        f"<a:lnStyleLst>{line(6350)}{line(12700)}{line(19050)}</a:lnStyleLst>"
        "<a:effectStyleLst>"
        + "<a:effectStyle><a:effectLst/></a:effectStyle>"
        * 3
        + f"</a:effectStyleLst><a:bgFillStyleLst>{fill}{fill}{fill}</a:bgFillStyleLst>"
        "</a:fmtScheme></a:themeElements></a:theme>"
    )


def _placeholders(*, master: bool = False) -> str:
    # a master must name the kind of its body placeholder; a layout may leave it generic
    body_ph = '<p:ph type="body" idx="1"/>' if master else '<p:ph idx="1"/>'
    return (
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
        + _text_shape(
            2,
            "Title Placeholder",
            685800,
            365760,
            10820400,
            1005840,
            '<a:p><a:r><a:rPr lang="en-US"/><a:t>Click to edit the title</a:t></a:r></a:p>',
            ph='<p:ph type="title"/>',
            anchor="ctr",
        )
        + _text_shape(
            3,
            "Content Placeholder",
            685800,
            1645920,
            10820400,
            4572000,
            '<a:p><a:r><a:rPr lang="en-US"/><a:t>Click to edit the text</a:t></a:r></a:p>',
            ph=body_ph,
        )
    )


def _tx_styles() -> str:
    def level(size: int, mar: int = 0, indent: int = 0, bullet: bool = False) -> str:
        bu = '<a:buFont typeface="Arial"/><a:buChar char="&#8226;"/>' if bullet else "<a:buNone/>"
        return (
            f'<a:lvl1pPr marL="{mar}" indent="{indent}" algn="l"><a:spcBef><a:spcPts val="600"/>'
            f'</a:spcBef>{bu}<a:defRPr sz="{size}" kern="1200"><a:solidFill><a:schemeClr val="tx1"/>'
            '</a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/>'
            '<a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr>'
        )

    return (
        "<p:txStyles>"
        f"<p:titleStyle>{level(3600)}</p:titleStyle>"
        f"<p:bodyStyle>{level(2400, 342900, -342900, True)}</p:bodyStyle>"
        f"<p:otherStyle>{level(1800)}</p:otherStyle>"
        "</p:txStyles>"
    )


def build_pptx(deck_title: str, subtitle: str, slides: list[SlideSpec]) -> bytes:
    """A 16:9 presentation: a dark title slide, then one clean slide per outline entry."""
    deck_title = deck_title.strip() or slides[0].title
    first = SlideSpec(deck_title, [])
    if slides and slides[0].title.strip().lower() in ("title", deck_title.lower()):
        first, slides = slides[0], slides[1:]
        first = SlideSpec(deck_title, first.bullets)
    all_slides = [first, *slides]
    n = len(all_slides)
    parts: dict[str, str] = {
        "[Content_Types].xml": (
            f'{_HEAD}<Types xmlns="{_PKG}/content-types">'
            f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>'
            '<Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>'
            '<Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>'
            + "".join(
                f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
                for i in range(1, n + 1)
            )
            + "</Types>"
        ),
        "_rels/.rels": _rels(("rId1", "officeDocument", "ppt/presentation.xml")),
        "ppt/presentation.xml": (
            f'{_HEAD}<p:presentation xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            "<p:sldIdLst>"
            + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, n + 1))
            + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": _rels(
            ("rId1", "slideMaster", "slideMasters/slideMaster1.xml"),
            *((f"rId{i + 1}", "slide", f"slides/slide{i}.xml") for i in range(1, n + 1)),
            (f"rId{n + 2}", "presProps", "presProps.xml"),
            (f"rId{n + 3}", "viewProps", "viewProps.xml"),
            (f"rId{n + 4}", "theme", "theme/theme1.xml"),
            (f"rId{n + 5}", "tableStyles", "tableStyles.xml"),
        ),
        "ppt/presProps.xml": f'{_HEAD}<p:presentationPr xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"/>',
        "ppt/viewProps.xml": (
            f'{_HEAD}<p:viewPr xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
            '<p:gridSpacing cx="76200" cy="76200"/></p:viewPr>'
        ),
        "ppt/tableStyles.xml": (
            f'{_HEAD}<a:tblStyleLst xmlns:a="{_A}" def="{{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}}"/>'
        ),
        "ppt/slideMasters/slideMaster1.xml": (
            f'{_HEAD}<p:sldMaster xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"><p:cSld>'
            '<p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
            f"<p:spTree>{_placeholders(master=True)}</p:spTree></p:cSld>"
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" '
            'folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
            "</p:sldLayoutIdLst>" + _tx_styles() + "</p:sldMaster>"
        ),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "theme", "../theme/theme1.xml"),
        ),
        "ppt/slideLayouts/slideLayout1.xml": (
            f'{_HEAD}<p:sldLayout xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}" type="obj" preserve="1">'
            f'<p:cSld name="Title and Content"><p:spTree>{_placeholders()}</p:spTree></p:cSld>'
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
        ),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels(
            ("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")
        ),
        "ppt/theme/theme1.xml": _theme_xml(),
    }
    for i, spec in enumerate(all_slides):
        parts[f"ppt/slides/slide{i + 1}.xml"] = _slide_xml(i, spec, deck_title, subtitle)
        parts[f"ppt/slides/_rels/slide{i + 1}.xml.rels"] = _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")
        )
    return _zip(parts)


# ---- Excel ----------------------------------------------------------------------------------------

_NUMBER = re.compile(r"^-?\d{1,15}(?:\.\d+)?$")
_SAFE_FORMULA = re.compile(r"^=[A-Za-z0-9_().,:+\-*/^<>=&% \"$]+$")
_BLOCKED_FUNCTIONS = re.compile(
    r"\b(WEBSERVICE|CALL|REGISTER|REGISTER\.ID|EXEC|HYPERLINK|DDE|SHELL|INDIRECT|FILTERXML|"
    r"ENCODEURL|RTD|IMPORTDATA|TEXTJOIN\s*\(\s*\"[^\"]*\"\s*,\s*TRUE\s*,\s*\")\b",
    re.IGNORECASE,
)


def parse_table(table: str) -> list[list[str]]:
    lines = [ln for ln in table.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("the table is empty: give one row per line, cells separated by |")
    delimiter = (
        "|"
        if any("|" in ln for ln in lines)
        else ("\t" if any("\t" in ln for ln in lines) else ",")
    )
    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip(delimiter).split(delimiter)]
        # a markdown separator row such as |---|---| is not data
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
            continue
        rows.append(cells)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"at most {MAX_ROWS} rows")
    width = max(len(r) for r in rows)
    if width > MAX_COLUMNS:
        raise ValueError(f"at most {MAX_COLUMNS} columns")
    return [r + [""] * (width - len(r)) for r in rows]


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, text: str, header: bool) -> str:
    style = ' s="1"' if header else ""
    if not text:
        return f'<c r="{ref}"{style}/>'
    if not header and _NUMBER.match(text):
        return f'<c r="{ref}"><v>{text}</v></c>'
    if not header and text.startswith("="):
        if _SAFE_FORMULA.match(text) and not _BLOCKED_FUNCTIONS.search(text) and len(text) < 400:
            return f'<c r="{ref}"><f>{_x(text[1:])}</f></c>'
        text = "'" + text  # shown as text, never calculated
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{_x(text)}</t></is></c>'


def sheet_name(title: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", " ", title).strip().strip("'") or "Sheet1"
    return re.sub(r"\s+", " ", cleaned)[:31]


def build_xlsx(title: str, rows: list[list[str]]) -> bytes:
    """A workbook with one sheet: bold header row (frozen), sized columns, numbers and formulas."""
    widths = []
    for c in range(len(rows[0])):
        longest = max(len(r[c]) for r in rows[:200])
        widths.append(min(60, max(8, longest + 2)))
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(widths)
    )
    body = ""
    for r, row in enumerate(rows, start=1):
        cells = "".join(_cell(f"{_column_name(c)}{r}", v, r == 1) for c, v in enumerate(row))
        body += f'<row r="{r}">{cells}</row>'
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    pane = (
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        if len(rows) > 1
        else ""
    )
    parts = {
        "[Content_Types].xml": (
            f'{_HEAD}<Types xmlns="{_PKG}/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": _rels(("rId1", "officeDocument", "xl/workbook.xml")),
        "xl/workbook.xml": (
            f'{_HEAD}<workbook xmlns="{main}" xmlns:r="{_R}"><sheets>'
            f'<sheet name="{_x(sheet_name(title))}" sheetId="1" r:id="rId1"/></sheets>'
            '<calcPr fullCalcOnLoad="1"/></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": _rels(
            ("rId1", "worksheet", "worksheets/sheet1.xml"), ("rId2", "styles", "styles.xml")
        ),
        "xl/styles.xml": (
            f'{_HEAD}<styleSheet xmlns="{main}">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill>'
            f'<fill><patternFill patternType="solid"><fgColor rgb="FF{NAVY}"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
            "</cellXfs></styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": (
            f'{_HEAD}<worksheet xmlns="{main}">{pane}<cols>{cols}</cols>'
            f"<sheetData>{body}</sheetData></worksheet>"
        ),
    }
    return _zip(parts)
