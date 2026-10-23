from collections.abc import Iterable
from datetime import datetime, time
from pathlib import Path

from openpyxl import load_workbook

from sdi.loaders import DocumentContent, register


@register(".xlsx")
def load_excel(path: Path) -> DocumentContent:
    # data_only reads the value a formula last computed, not the formula itself.
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = [
            (sheet.title, _row_lines(sheet.iter_rows(values_only=True)))
            for sheet in workbook.worksheets
        ]
    finally:
        workbook.close()
    # Like a PDF without a text layer, a workbook with no values gets None rather than bare
    # sheet markers, so it can't pass as a text Source.
    text = None
    if any(lines for _, lines in sheets):
        text = "\n\n".join(f"--- sheet {title} ---\n" + "\n".join(lines) for title, lines in sheets)
    return DocumentContent(
        source_path=str(path),
        text=text,
        metadata={"file_type": "xlsx", "sheet_count": len(sheets)},
    )


def _row_lines(rows: Iterable[tuple[object, ...]]) -> list[str]:
    # Each row is one line with cells separated by " | ", the same table rendering as the
    # email loader, so a price stays next to its product. Inner empty cells are kept so
    # columns still line up with their header; trailing ones and blank rows are dropped.
    lines = []
    for row in rows:
        cells = [_cell(value) for value in row]
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            lines.append(" | ".join(cells))
    return lines


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        # Excel stores a date as a datetime at midnight; print it as the date it was typed as.
        return value.date().isoformat() if value.time() == time() else value.isoformat(sep=" ")
    return str(value).strip()
