"""Safe, editable print outputs from authored template text."""
import html

from onpf.exports.markdown import render_markdown
from onpf.exports.odt import render_odt
from onpf.errors import DomainError


def display_document(item):
    rights = {'key': 'rights', 'label': 'Rights and notices',
              'text': f"License: {item['license']}\nPermission basis: {item['permission_basis']}\n{item['notices']}".strip()}
    return {'title': item['title'], 'sections': [*item['sections'], rights],
            'attachment':item.get('attachment'),
            'layout':(item.get('structured') or {}).get('layout','letter')}


def document_file(item, extension, *, page_limit=100):
    document = display_document(item)
    if extension=='pdf':
        from onpf.additional_documents.pdf import render
        return render(item,page_limit=page_limit), 'application/pdf'
    if extension == 'md':
        return render_markdown(document).encode('utf-8'), 'text/markdown; charset=utf-8'
    if extension == 'odt':
        return render_odt(document), 'application/vnd.oasis.opendocument.text'
    if extension != 'html':
        raise DomainError('not_found', 'This document format is unavailable.', 404)
    layout=(item.get('structured') or {}).get('layout','letter')
    page={'letter':'letter','a4':'A4','card':'6in 4in','poster':'11in 17in'}.get(layout,'letter')
    style = ('body{font:13pt/1.4 Arial,sans-serif;color:#17221b;max-width:7.2in;margin:auto;padding:.35in}'
             'h1{font-size:28pt;line-height:1.1}h2{font-size:16pt;margin:.28in 0 .06in}'
             'section{break-inside:avoid}p{white-space:pre-wrap;margin:.05in 0 .16in;overflow-wrap:anywhere}'
             '.recipe-card{border:2px solid #385d42;padding:.28in}.pantry-poster h1{font-size:42pt;text-transform:uppercase}'
             '.pantry-poster section:nth-of-type(2) p{font-size:23pt}.rights{font-size:9pt;border-top:1px solid #888;margin-top:.35in}'
             '@page{size:letter;margin:.55in}@media print{body{padding:0}}')
    style=style.replace('size:letter','size:'+page)
    kind = html.escape(item['template_key'])
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>' + html.escape(item['title']) + '</title><style>' + style + '</style></head>',
             '<body class="' + kind + '"><h1>' + html.escape(item['title']) + '</h1>']
    for section in document['sections']:
        block=section.get('block',{})
        if block.get('type')=='table':
            parts.append('<section><h2>'+html.escape(section['label'])+'</h2><p>'+html.escape(block.get('review_guard',''))+'</p><table>')
            for row in block['rows']:
                parts.append('<tr>'+''.join('<td>'+html.escape(cell)+'</td>' for cell in row)+'</tr>')
            parts.append('</table></section>')
            continue
        if block.get('type')=='image' and item.get('attachment'):
            from onpf.additional_documents.service import attachment_bytes
            attachment_bytes(item['attachment'])
            if item['attachment']['mime'] not in {'image/png','image/jpeg'}:
                raise DomainError('invalid_image','Use a validated local PNG or JPEG attachment.',422)
            parts.append('<section><p>'+html.escape(block.get('review_guard',''))+'</p><img style="max-width:100%;max-height:6in" alt="'+html.escape(block.get('text',''))+'" src="data:'+item['attachment']['mime']+';base64,'+item['attachment']['data']+'"></section>')
            continue
        parts.extend(['<section' + (' class="rights"' if section['key'] == 'rights' else '') + '><h2>'
                      + html.escape(section['label']) + '</h2><p>' + html.escape(section['text']) + '</p></section>'])
    parts.append('</body></html>')
    return '\n'.join(parts).encode('utf-8'), 'text/html; charset=utf-8'
