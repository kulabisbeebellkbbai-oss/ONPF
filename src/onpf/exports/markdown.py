"""Editable Markdown containing literal, inert authored text."""
import html
import re
from onpf.db import Record


def literal(value) -> str:
    value = '' if value is None else str(value)
    value = html.escape(value, quote=False)
    return re.sub(r'([\\`*_{}\[\]()#!|>+~.\-])', r'\\\1', value)


def render_markdown(document: Record) -> str:
    lines = ['# ' + literal(document['title']), '']
    for section in document['sections']:
        lines.extend(['## ' + literal(section['label']), ''])
        if section['text'].strip():
            lines.extend([literal(section['text']).replace('\n', '  \n'), ''])
        else:
            lines.extend(['Unresolved local requirement. Complete locally before use.', '', '________________________________________________________________', ''])
    if 'rows' in document:
        lines.extend(['## Budget rows', '', 'All costs are literal values; unknown values remain unknown.', '', '| Item | Quantity | Unit cost | Status | Notes |', '| --- | --- | --- | --- | --- |'])
        for row in document['rows']:
            lines.append('| ' + ' | '.join(literal(row[key] if row[key] is not None else 'Unknown').replace('\n', ' ') for key in ('item', 'quantity', 'unit_cost', 'cost_status', 'notes')) + ' |')
        lines.append('')
    return '\n'.join(lines) + '\n'
