"""PDF and DOCX extraction that keeps headings, order, pages, and tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

import pymupdf
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.config import get_settings


@dataclass
class ExtractedBlock:
    kind: str
    text: str
    heading_path: list[str]
    page: int | None = None
    heading_level: int | None = None


@dataclass
class ExtractionResult:
    blocks: list[ExtractedBlock] = field(default_factory=list)
    page_count: int = 0
    char_count: int = 0
    warnings: list[str] = field(default_factory=list)


def extract_document(filename: str, data: bytes) -> ExtractionResult:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _extract_pdf(data)
    if lower.endswith(".docx"):
        return _extract_docx(data)
    raise ValueError("unsupported type")


def _extract_pdf(data: bytes) -> ExtractionResult:
    threshold = get_settings().low_text_char_threshold
    document = pymupdf.open(stream=data, filetype="pdf")
    result = ExtractionResult(page_count=document.page_count)
    heading_stack: list[tuple[int, str]] = []
    try:
        for index, page in enumerate(document):
            page_number = index + 1
            plain = page.get_text("text") or ""
            if len(plain.strip()) < threshold:
                result.warnings.append(
                    f"Page {page_number} has little or no extractable text and was not indexed."
                )
                continue
            table_rects = []
            try:
                finder = page.find_tables()
                for table in finder.tables:
                    table_rects.append(table.bbox)
                    rows = table.extract() or []
                    rendered = _render_table_rows(rows)
                    if rendered:
                        result.blocks.append(
                            ExtractedBlock(
                                kind="table",
                                text=rendered,
                                heading_path=[name for _, name in heading_stack],
                                page=page_number,
                            )
                        )
            except Exception:
                table_rects = []
            page_dict = page.get_text("dict")
            sizes = []
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            sizes.append(float(span.get("size", 0)))
            body_size = sorted(sizes)[len(sizes) // 2] if sizes else 12
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                bbox = block.get("bbox")
                if bbox and _overlaps_tables(bbox, table_rects):
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(span.get("text", "") for span in spans).strip()
                    if not text:
                        continue
                    max_size = max(float(span.get("size", 0)) for span in spans) if spans else body_size
                    if max_size >= max(body_size + 2, body_size * 1.15) and len(text) <= 140:
                        level = 1 if max_size >= body_size + 6 else 2
                        heading_stack = _push_heading(heading_stack, level, text)
                        result.blocks.append(
                            ExtractedBlock(
                                kind="heading",
                                text=text,
                                heading_path=[name for _, name in heading_stack],
                                page=page_number,
                                heading_level=level,
                            )
                        )
                    else:
                        result.blocks.append(
                            ExtractedBlock(
                                kind="paragraph",
                                text=text,
                                heading_path=[name for _, name in heading_stack],
                                page=page_number,
                            )
                        )
    finally:
        document.close()
    result.char_count = sum(len(block.text) for block in result.blocks if block.kind != "heading")
    return result


def _extract_docx(data: bytes) -> ExtractionResult:
    threshold = get_settings().low_text_char_threshold
    document = Document(BytesIO(data))
    result = ExtractionResult(page_count=1)
    heading_stack: list[tuple[int, str]] = []
    section_chars = 0
    section_name = ""
    page_breaks = 0

    def close_section() -> None:
        nonlocal section_chars, section_name
        if section_name and section_chars < threshold:
            result.warnings.append(
                f"Section '{section_name}' has little or no extractable text and was not indexed."
            )
        section_chars = 0

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, document)
            style = paragraph.style.name if paragraph.style is not None else ""
            text = paragraph.text.strip()
            if "pageBreakBefore" in paragraph._p.xml or 'w:type="page"' in paragraph._p.xml:
                page_breaks += text.count("\f")
            if style.startswith("Heading") or style == "Title":
                close_section()
                level = 1
                tail = style.split()[-1]
                if tail.isdigit():
                    level = int(tail)
                heading_stack = _push_heading(heading_stack, level, text or style)
                section_name = text or style
                if text:
                    result.blocks.append(
                        ExtractedBlock(
                            kind="heading",
                            text=text,
                            heading_path=[name for _, name in heading_stack],
                            page=None,
                            heading_level=level,
                        )
                    )
                continue
            if not text:
                continue
            section_chars += len(text)
            result.blocks.append(
                ExtractedBlock(
                    kind="paragraph",
                    text=text,
                    heading_path=[name for _, name in heading_stack],
                    page=None,
                )
            )
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            rendered = _render_table_rows(rows)
            if rendered:
                section_chars += len(rendered)
                result.blocks.append(
                    ExtractedBlock(
                        kind="table",
                        text=rendered,
                        heading_path=[name for _, name in heading_stack],
                        page=None,
                    )
                )
    close_section()
    result.page_count = max(1, page_breaks + 1)
    result.char_count = sum(len(block.text) for block in result.blocks if block.kind != "heading")
    if result.char_count < threshold:
        result.warnings.append("The document has little or no extractable text and was not indexed.")
    return result


def _push_heading(stack: list[tuple[int, str]], level: int, text: str) -> list[tuple[int, str]]:
    updated = list(stack)
    while updated and updated[-1][0] >= level:
        updated.pop()
    updated.append((level, text))
    return updated


def _render_table_rows(rows: list) -> str:
    rendered = []
    for row in rows:
        cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
        if any(cells):
            rendered.append(" | ".join(cells))
    return "\n".join(rendered)


def _overlaps_tables(bbox, table_rects) -> bool:
    x0, y0, x1, y1 = bbox
    for rect in table_rects:
        rx0, ry0, rx1, ry1 = rect
        if x0 < rx1 and x1 > rx0 and y0 < ry1 and y1 > ry0:
            return True
    return False
