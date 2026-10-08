"""Spreadsheet-safe text values without evaluating formulas."""
import csv
import io
from onpf.db import Record


def _cell(value):
    text = '' if value is None else str(value)
    if text.startswith(('\t', '\r', '\n')) or text.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + text
    return text


def render_csv(rows: list[Record], columns: list[str]) -> str:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([_cell(column) for column in columns])
    writer.writerows([_cell(row.get(column)) for column in columns] for row in rows)
    return output.getvalue()
