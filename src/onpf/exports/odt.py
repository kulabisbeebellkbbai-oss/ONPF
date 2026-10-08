"""Editable plain-text ODT with deliberate print spacing and no active content."""
import io
from odf.opendocument import OpenDocumentText
from odf.style import Style, TextProperties, ParagraphProperties, PageLayout, PageLayoutProperties, MasterPage, TableColumnProperties, TableCellProperties, Footer, FontFace
from odf.text import H, P, LineBreak, S, Tab, PageNumber
from odf.table import Table, TableColumn, TableRow, TableCell, TableHeaderRows
from onpf.db import Record
from onpf.errors import DomainError


def validate_literal_text(value):
    if isinstance(value, str):
        if any(not (char in '\t\n\r' or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF) for char in value):
            raise DomainError('invalid_export_text', 'An editable field contains a character unsupported by plain-text documents.', 422)
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_literal_text(key)
            validate_literal_text(item)
    elif isinstance(value, list):
        for item in value:
            validate_literal_text(item)


def _paragraph(parent, value, style='Body'):
    paragraph = P(stylename=style)
    # odfpy escapes XML; explicit spaces/newlines preserve the literal source text.
    import re
    for token in re.split(r'(\n|\t| +)', str(value)):
        if token == '\n':
            paragraph.addElement(LineBreak())
        elif token == '\t':
            paragraph.addElement(Tab())
        elif token.startswith(' '):
            paragraph.addElement(S(c=len(token)))
        elif token:
            paragraph.addText(token)
    parent.addElement(paragraph)


def render_odt(document: Record) -> bytes:
    validate_literal_text(document)
    output = OpenDocumentText()
    output.fontfacedecls.addElement(FontFace(name='Liberation Sans', fontfamily='Liberation Sans'))
    for name, family, properties in [
        ('Body', 'paragraph', ParagraphProperties(marginbottom='0.12in', lineheight='125%')),
        ('Heading1', 'paragraph', ParagraphProperties(margintop='0.2in', marginbottom='0.15in', keepwithnext='always')),
        ('Heading2', 'paragraph', ParagraphProperties(margintop='0.18in', marginbottom='0.10in', keepwithnext='always')),
        ('EmptyPrompt', 'paragraph', ParagraphProperties(marginbottom='0.12in', keepwithnext='always')),
        ('WritingNext', 'paragraph', ParagraphProperties(lineheight='0.3in', marginbottom='0.12in', borderbottom='0.5pt solid #9aaea1', joinborder='false', keepwithnext='always')),
        ('Writing', 'paragraph', ParagraphProperties(lineheight='0.3in', marginbottom='0.12in', borderbottom='0.5pt solid #9aaea1', joinborder='false')),
        ('Cell', 'table-cell', TableCellProperties(padding='0.06in', border='0.5pt solid #9aaea1')),
    ]:
        style = Style(name=name, family=family)
        style.addElement(properties)
        if family == 'paragraph':
            style.addElement(TextProperties(fontname='Liberation Sans', fontsize='18pt' if name == 'Heading1' else ('13pt' if name == 'Heading2' else '10pt')))
        (output.styles if family == 'paragraph' else output.automaticstyles).addElement(style)
    layout = PageLayout(name='Letter')
    width,height={'letter':('8.5in','11in'),'a4':('21cm','29.7cm'),'card':('6in','4in'),'poster':('11in','17in')}.get(document.get('layout','letter'),('8.5in','11in'))
    margin='0.3in' if document.get('layout')=='card' else '0.65in'
    layout.addElement(PageLayoutProperties(pagewidth=width, pageheight=height, margintop=margin, marginbottom=margin, marginleft=margin, marginright=margin))
    output.automaticstyles.addElement(layout)
    master = MasterPage(name='Standard', pagelayoutname=layout)
    footer = Footer()
    footer_text = P(stylename='Body', text='ONPF | Page ')
    footer_text.addElement(PageNumber(selectpage='current'))
    footer.addElement(footer_text)
    master.addElement(footer)
    output.masterstyles.addElement(master)
    output.text.addElement(H(outlinelevel=1, stylename='Heading1', text=document['title']))
    for section in document['sections']:
        if section['label']:
            output.text.addElement(H(outlinelevel=2, stylename='Heading2', text=section['label']))
        block=section.get('block',{})
        if block.get('type')=='image' and document.get('attachment'):
            from odf.draw import Frame,Image as DrawImage
            from onpf.additional_documents.service import attachment_bytes
            from PIL import Image
            attachment=document['attachment']
            raw=attachment_bytes(attachment)
            if attachment['mime'] not in {'image/png','image/jpeg'}:
                raise DomainError('invalid_image','Use a validated local PNG or JPEG image.',422)
            with Image.open(io.BytesIO(raw)) as image:
                width,height=image.size
            maximum_width=5 if document.get('layout')=='card' else 6.5
            ratio=min(maximum_width/width,3/height)
            frame=Frame(width=f'{width*ratio}in',height=f'{height*ratio}in',anchortype='as-char')
            href=output.addPictureFromString(raw,attachment['mime'])
            frame.addElement(DrawImage(href=href,type='simple',show='embed',actuate='onLoad'))
            paragraph=P(stylename='Body'); paragraph.addElement(frame); output.text.addElement(paragraph)
            if block.get('review_guard'):
                _paragraph(output.text,block['review_guard'])
            _paragraph(output.text,block.get('text',''))
            continue
        if block.get('type')=='heading':
            if block.get('review_guard'):
                _paragraph(output.text,block['review_guard'])
            continue
        if block.get('type')=='table':
            if block.get('review_guard'):
                _paragraph(output.text,block['review_guard'])
            table=Table(name='ContentTable'+section['key'])
            for values in block['rows']:
                row_element=TableRow()
                for value in values:
                    cell=TableCell(stylename='Cell',valuetype='string')
                    _paragraph(cell,value)
                    row_element.addElement(cell)
                table.addElement(row_element)
            output.text.addElement(table)
            continue
        if section['text'].strip():
            for paragraph in section['text'].split('\n\n'):
                _paragraph(output.text, paragraph)
        else:
            _paragraph(output.text, 'Unresolved local requirement. Complete locally before use.', 'EmptyPrompt')
            for index in range(3):
                _paragraph(output.text, '', 'Writing' if index == 2 else 'WritingNext')
    if 'rows' in document:
        output.text.addElement(H(outlinelevel=2, stylename='Heading2', text='Budget rows'))
        _paragraph(output.text, 'Costs marked estimated are planning values. Blank unit costs are unknown. Check currency and planning period locally.')
        table = Table(name='Budget')
        for index, width in enumerate(('2.3in', '0.9in', '0.9in', '0.9in', '2.1in')):
            column_style = Style(name=f'BudgetColumn{index}', family='table-column')
            column_style.addElement(TableColumnProperties(columnwidth=width))
            output.automaticstyles.addElement(column_style)
            table.addElement(TableColumn(stylename=column_style))
        header = TableHeaderRows()
        table.addElement(header)
        for index, values in enumerate([['Item', 'Quantity', 'Unit cost', 'Status', 'Notes']] + [[row[key] if row[key] is not None else 'Unknown' for key in ('item', 'quantity', 'unit_cost', 'cost_status', 'notes')] for row in document['rows']]):
            row_element = TableRow()
            for value in values:
                cell = TableCell(stylename='Cell', valuetype='string')
                _paragraph(cell, value)
                row_element.addElement(cell)
            (table if index else header).addElement(row_element)
        output.text.addElement(table)
    stream = io.BytesIO()
    output.write(stream)
    return stream.getvalue()
