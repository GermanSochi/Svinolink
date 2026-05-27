from __future__ import annotations

import io

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader


def extract_pdf_text(data: bytes, *, max_pages: int = 10) -> str:
    buf = io.BytesIO(data)
    reader = PdfReader(buf)
    chunks: list[str] = []
    for page in reader.pages[:max_pages]:
        t = (page.extract_text() or "").strip()
        if t:
            chunks.append(t)
    return "\n".join(chunks).strip()


def extract_docx_text(data: bytes) -> str:
    buf = io.BytesIO(data)
    d = Document(buf)
    return "\n".join(p.text for p in d.paragraphs if p.text).strip()


def extract_xlsx_preview(data: bytes, *, max_rows: int = 15, max_cols: int = 8) -> str:
    buf = io.BytesIO(data)
    wb = load_workbook(buf, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows: list[str] = []
    for r_i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if r_i > max_rows:
            break
        cells = ["" if v is None else str(v) for v in row[:max_cols]]
        rows.append(" | ".join(cells).strip())
    return "\n".join(r for r in rows if r).strip()

