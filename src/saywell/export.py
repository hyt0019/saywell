"""Saving a meeting as a file: its minutes, its transcript, or both, as TXT, Markdown, Word or PDF.

Everything starts from the same blocks (minutes.outline plus the transcript's paragraphs, as the
page shows them), so the formats say the same thing. Word files are written by hand (a .docx is a zip of a few XML parts) and PDFs
with Qt, so neither needs a new dependency.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .meeting import LABELS, Line, clock, paragraphs
from .minutes import Minutes, outline, to_markdown, to_text, todo_text

FORMATS = {
    "docx": ("Word 文档", "Word 文档 (*.docx)"),
    "pdf": ("PDF", "PDF (*.pdf)"),
    "md": ("Markdown", "Markdown (*.md)"),
    "txt": ("纯文本", "纯文本 (*.txt)"),
}
FONT = "Microsoft YaHei"
LATIN = "Segoe UI"


def document_blocks(
    info: dict, minutes: Minutes | None, lines: list[Line], labels: bool, transcript: bool
) -> list[tuple[str, object]]:
    """The minutes (when there are some), then the transcript when asked for or when there are no
    minutes; a transcript-only file still starts with the meeting's title and details."""
    if minutes is not None:
        blocks = outline(minutes, info)
    else:
        from .minutes import Notes

        blocks = outline(Minutes(Notes(), "", 0, 0), info)
        blocks[1] = ("meta", blocks[1][1].replace(" · 通用会议", ""))
        transcript = True
    if transcript and lines:
        blocks.append(("heading", "会议文字"))
        for paragraph in paragraphs(sorted(lines, key=lambda line: (line.start, line.end)), labels):
            who = LABELS.get(paragraph.who, "") if labels else ""
            blocks.append(("line", (clock(paragraph.start), who, paragraph.text)))
    return blocks


def file_name(title: str) -> str:
    """A file name from the meeting's title, without what Windows does not allow."""
    name = re.sub(r'[\\/*?"<>|]', "", title.replace(":", "："))
    return name.strip(" .") or "会议"


def save(path: Path, blocks: list[tuple[str, object]]) -> None:
    kind = path.suffix.lower().lstrip(".")
    if kind == "txt":
        text = to_text([block for block in blocks if block[0] != "line"]) + transcript_text(blocks, "txt")
        path.write_text(text.rstrip() + "\n", encoding="utf-8-sig", newline="\r\n")
    elif kind == "md":
        text = to_markdown([block for block in blocks if block[0] != "line"]) + "\n" + transcript_text(blocks, "md")
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
    elif kind == "docx":
        write_docx(path, blocks)
    elif kind == "pdf":
        write_pdf(path, blocks)
    else:
        raise ValueError(f"unknown format: {kind}")


def transcript_text(blocks: list[tuple[str, object]], kind: str) -> str:
    rows = []
    for block_kind, value in blocks:
        if block_kind != "line":
            continue
        when, who, text = value
        if kind == "md":
            rows.append(f"**{when}{' ' + who if who else ''}**　{text}")
        else:
            rows.append(f"[{when}] {who}：{text}" if who else f"[{when}] {text}")
    # Paragraphs run long: a blank line between them, in either format.
    return "\n\n".join(rows) + "\n" if rows else ""


# --- Word -------------------------------------------------------------------------------------

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

PACKAGE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{title}</dc:title><dc:creator>好好说 Saywell</dc:creator>
</cp:coreProperties>"""


def style(style_id: str, name: str, run: str, paragraph: str = "", based: str = "Normal") -> str:
    base = f'<w:basedOn w:val="{based}"/>' if based else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>{base}'
        f'<w:qFormat/><w:pPr>{paragraph}</w:pPr><w:rPr>{run}</w:rPr></w:style>'
    )


STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    f'<w:rFonts w:ascii="{LATIN}" w:hAnsi="{LATIN}" w:eastAsia="{FONT}" w:cs="{LATIN}"/>'
    '<w:color w:val="1D2240"/><w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:eastAsia="zh-CN"/>'
    "</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>"
    '<w:spacing w:after="80" w:line="336" w:lineRule="auto"/>'
    "</w:pPr></w:pPrDefault></w:docDefaults>"
    + style("Normal", "Normal", "", based="")
    + style("Title", "Title", '<w:b/><w:sz w:val="36"/><w:szCs w:val="36"/>', '<w:spacing w:after="60"/>')
    + style("Subtitle", "Subtitle", '<w:color w:val="958C7F"/><w:sz w:val="18"/>', '<w:spacing w:after="240"/>')
    + style(
        "Heading1", "heading 1", '<w:b/><w:sz w:val="26"/><w:szCs w:val="26"/>',
        '<w:keepNext/><w:spacing w:before="320" w:after="100"/><w:outlineLvl w:val="0"/>',
    )
    + style("ListItem", "List Paragraph", "", '<w:ind w:left="360" w:hanging="240"/>')
    + style("Points", "Chapter Points", '<w:color w:val="47413A"/>', '<w:spacing w:after="140"/>')
    + style("Line", "Transcript", "", '<w:spacing w:after="160"/>')
    + "</w:styles>"
)


def run(text: str, bold: bool = False, color: str = "") -> str:
    props = ("<w:b/>" if bold else "") + (f'<w:color w:val="{color}"/>' if color else "")
    return f'<w:r>{f"<w:rPr>{props}</w:rPr>" if props else ""}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph(style_id: str, *runs: str) -> str:
    return f'<w:p><w:pPr><w:pStyle w:val="{style_id}"/></w:pPr>{"".join(runs)}</w:p>'


def docx_body(blocks: list[tuple[str, object]]) -> str:
    out = []
    for kind, value in blocks:
        if kind == "title":
            out.append(paragraph("Title", run(value)))
        elif kind == "meta":
            out.append(paragraph("Subtitle", run(value)))
        elif kind == "heading":
            out.append(paragraph("Heading1", run(value)))
        elif kind == "paragraph":
            out.append(paragraph("Normal", run(value)))
        elif kind == "chapter":
            when, title, points = value
            out.append(paragraph("Normal", run(when + "  ", True, "3C488E"), run(title, True)))
            if points:
                out.append(paragraph("Points", run(points)))
        elif kind == "item":
            out.append(paragraph("ListItem", run("• "), run(value)))
        elif kind == "todo":
            out.append(paragraph("ListItem", run("☐ "), run(todo_text(*value))))
        elif kind == "line":
            when, who, text = value
            head = f"{when} {who}" if who else when
            out.append(paragraph("Line", run(head + "  ", True, "958C7F"), run(text)))
    return "".join(out)


def write_docx(path: Path, blocks: list[tuple[str, object]]) -> None:
    title = next((value for kind, value in blocks if kind == "title"), "会议")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + docx_body(blocks)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1300" w:bottom="1440" w:left="1300" w:header="720" w:footer="720" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    temp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", PACKAGE_RELS)
        archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", STYLES)
        archive.writestr("docProps/core.xml", CORE.format(title=escape(title)))
    temp.replace(path)


# --- PDF --------------------------------------------------------------------------------------


def print_html(blocks: list[tuple[str, object]]) -> str:
    """The blocks as HTML for Qt's PDF output, in the app's colours. Font sizes are in points;
    margins must be in pixels (Qt's rich text ignores other units there), counted at 96 dpi."""
    body = "line-height:135%"
    out = []
    for kind, value in blocks:
        if kind == "title":
            out.append(f"<p style='font-size:18pt; font-weight:700; margin:0 0 4px 0'>{html.escape(value)}</p>")
        elif kind == "meta":
            out.append(f"<p style='font-size:9pt; color:#958C7F; margin:0 0 8px 0'>{html.escape(value)}</p>")
        elif kind == "heading":
            out.append(f"<p style='font-size:13pt; font-weight:700; margin:22px 0 8px 0'>{html.escape(value)}</p>")
        elif kind == "paragraph":
            out.append(f"<p style='margin:0 0 6px 0; {body}'>{html.escape(value)}</p>")
        elif kind == "chapter":
            when, title, points = value
            out.append(
                f"<p style='margin:0 0 2px 0; {body}'>"
                f"<span style='color:#3C488E; font-weight:600'>{html.escape(when)}</span>&nbsp;&nbsp;"
                f"<b>{html.escape(title)}</b></p>"
                + (f"<p style='margin:0 0 10px 0; color:#47413A; {body}'>{html.escape(points)}</p>" if points else "")
            )
        elif kind == "item":
            out.append(f"<p style='margin:0 0 6px 0; {body}'>•&nbsp;&nbsp;{html.escape(value)}</p>")
        elif kind == "todo":
            out.append(f"<p style='margin:0 0 6px 0; {body}'>☐&nbsp;&nbsp;{html.escape(todo_text(*value))}</p>")
        elif kind == "line":
            when, who, text = value
            head = html.escape(f"{when} {who}" if who else when)
            out.append(
                f"<p style='margin:0 0 9px 0; {body}'><span style='color:#958C7F; font-weight:600'>"
                f"{head}</span>&nbsp;&nbsp;{html.escape(text)}</p>"
            )
    return "\n".join(out)


def write_pdf(path: Path, blocks: list[tuple[str, object]]) -> None:
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument

    title = next((value for kind, value in blocks if kind == "title"), "会议")
    temp = path.with_name(path.name + ".tmp")
    writer = QPdfWriter(str(temp))
    writer.setTitle(title)
    writer.setCreator("好好说 Saywell")
    writer.setResolution(300)
    # No margins here: without a page size of its own, the document is laid out by Qt at the
    # writer's resolution, with 2 cm margins and page numbers at the foot of each page. (A page
    # size set here would be taken in screen pixels and the text scaled up to fill the page.)
    writer.setPageLayout(QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
        QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter,
    ))
    document = QTextDocument()
    font = QFont(FONT)
    font.setPointSizeF(10.5)
    document.setDefaultFont(font)
    document.setHtml(print_html(blocks))
    document.print_(writer)
    del writer  # Closes the file.
    if not temp.is_file() or temp.stat().st_size == 0:
        raise OSError("PDF was not written")
    temp.replace(path)
