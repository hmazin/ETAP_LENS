"""
Excel (.xlsx) export helpers - generic table export and the aggregated
violations/alerts report. CSV export is handled client-side (no server
round-trip needed for plain text); this module exists because a real .xlsx
needs an actual library, not string-building.
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="2563EB")
HEADER_FONT = Font(bold=True, color="FFFFFF")
CRITICAL_FILL = PatternFill("solid", fgColor="FFCDD2")


def _autosize(ws, columns, rows, sample=200):
    for i, col in enumerate(columns, 1):
        longest = len(str(col))
        for row in rows[:sample]:
            v = row.get(col)
            if v is not None:
                longest = max(longest, len(str(v)))
        ws.column_dimensions[get_column_letter(i)].width = min(longest + 2, 45)


def _write_sheet(ws, columns, rows, highlight_col=None, highlight_values=("critical",)):
    ws.append(columns)
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
    ws.freeze_panes = "A2"

    highlight_idx = columns.index(highlight_col) if highlight_col in columns else None
    for row in rows:
        ws.append([row.get(c) for c in columns])
        if highlight_idx is not None and str(row.get(highlight_col, "")).lower() in highlight_values:
            for cell in ws[ws.max_row]:
                cell.fill = CRITICAL_FILL

    _autosize(ws, columns, rows)


def table_to_xlsx_bytes(columns, rows, sheet_name="Data"):
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Data")[:31]
    _write_sheet(ws, columns, rows)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def multi_sheet_xlsx_bytes(sheets):
    """sheets: list of (title, columns, rows) tuples - one sheet each.
    Rows whose AlertType is "Critical" are highlighted."""
    wb = Workbook()
    wb.remove(wb.active)
    for title, columns, rows in sheets:
        ws = wb.create_sheet(title=title[:31])
        _write_sheet(ws, columns, rows, highlight_col="AlertType")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
