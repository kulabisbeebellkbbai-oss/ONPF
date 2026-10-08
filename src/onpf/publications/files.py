"""Downloads generated only from the frozen public publication source."""
import hashlib
import html
import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from onpf.exports.csv import render_csv
from onpf.exports.markdown import render_markdown
from onpf.exports.odt import render_odt
from onpf.exports.package import MIT_0_LICENSE
from onpf.exports.projection import DOCUMENT_KEYS, ROW_FIELDS
from onpf.errors import DomainError
from onpf.additional_documents.files import document_file as additional_document_file
from onpf.additional_documents.service import attachment_bytes


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def _document(source, key):
    if key not in DOCUMENT_KEYS:
        raise DomainError('not_found', 'This public document is unavailable.', 404)
    document = source['documents'][key]
    heading = {'key': 'publication', 'label': 'Published project version',
               'text': f"{source['title']} · version {source['publication_version']} · {source['approval_status']} design · published {source['published_at']}"}
    permission = {'key': 'permission', 'label': 'Operating permission',
                  'text': f"Recorded operating status: {source['operating_status']}. Filed evidence status at publication: {source.get('permission_evidence_status', 'not recorded')}. Public design approval and publication do not verify site permission."}
    return {**document, 'sections': [heading, permission, *document['sections']]}


def printable_html(source, key):
    document = _document(source, key)
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>' + html.escape(document['title']) + '</title>',
             '<style>body{font:11pt/1.5 Arial,sans-serif;max-width:48rem;margin:2rem auto;color:#222}h1,h2{break-after:avoid}section{break-inside:avoid}p{white-space:pre-wrap}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #777;padding:.4rem;vertical-align:top;overflow-wrap:anywhere}tr{break-inside:avoid}thead{display:table-header-group}@page{size:letter;margin:.7in}@media print{body{margin:0;max-width:none}}</style></head><body>',
             '<h1>' + html.escape(document['title']) + '</h1>']
    for section in document['sections']:
        parts.append('<section><h2>' + html.escape(section['label']) + '</h2><p>' + html.escape(section['text'] or 'Not yet drafted') + '</p></section>')
    if key == 'budget' and document.get('rows'):
        parts.append('<table><thead><tr>' + ''.join('<th>' + html.escape(label) + '</th>' for label in ('Item', 'Quantity', 'Unit cost', 'Status', 'Notes')) + '</tr></thead><tbody>')
        for row in document['rows']:
            parts.append('<tr>' + ''.join('<td>' + html.escape(str(row[field]) if row[field] is not None else 'Unknown') + '</td>' for field in ROW_FIELDS) + '</tr>')
        parts.append('</tbody></table>')
    parts.append('<footer><p>Project-owned content: MIT-0. Review local requirements and third-party notices before reuse.</p></footer></body></html>')
    return '\n'.join(parts).encode('utf-8')


def document_file(source, key, extension):
    document = _document(source, key)
    if extension == 'md':
        return render_markdown(document).encode('utf-8'), 'text/markdown; charset=utf-8'
    if extension == 'odt':
        return render_odt(document), 'application/vnd.oasis.opendocument.text'
    if extension == 'html':
        return printable_html(source, key), 'text/html; charset=utf-8'
    raise DomainError('not_found', 'This public document format is unavailable.', 404)


def package_bytes(source):
    files = {'source/program.json': _json({key: value for key, value in source.items() if key != 'source_hash'}),
             'LICENSE': MIT_0_LICENSE.encode('utf-8')}
    for key in DOCUMENT_KEYS:
        for extension in ('md', 'odt', 'html'):
            files[f'documents/{key}.{extension}'] = document_file(source, key, extension)[0]
    for item in source.get('additional_documents', []):
        for extension in ('md', 'odt', 'html'):
            files[f"additional/{item['id']}.{extension}"] = additional_document_file(item, extension)[0]
        if item['attachment']:
            extension = item['attachment']['name'].rsplit('.', 1)[-1].lower()
            files[f"additional/attachments/{item['id']}.{extension}"] = attachment_bytes(item['attachment'])
    files['tables/budget.csv'] = render_csv(source['documents']['budget'].get('rows', []), list(ROW_FIELDS)).encode('utf-8')
    notices = ['# Public project notices', '', 'The seven framework documents are offered under MIT-0. Additional program media and selected materials retain their recorded licenses and permissions below.', '']
    for material in source['materials']:
        notices.extend([f"## {material['title']}", '', f"License: {material['license']}", '', f"Permission basis: {material['permission_basis']}", '', material['notices'], ''])
    for item in source.get('additional_documents', []):
        notices.extend([f"## Additional document: {item['title']}", '', f"License: {item['license']}",
                        '', f"Permission basis: {item['permission_basis']}", '', item['notices'], ''])
    files['NOTICES.md'] = ('\n'.join(notices) + '\n').encode('utf-8')
    files['README.md'] = (f"# {source['title']}\n\nPublished version {source['publication_version']} ({source['approval_status']} design). "
                          f"Recorded operating status: {source['operating_status']}; filed evidence status at publication: {source.get('permission_evidence_status', 'not recorded')}. Actual permission is not verified by publication.\n\n"
                          'Documents, including selected additional program media, are available in Markdown, ODT, and printable HTML. Attached files keep their recorded terms. Review literal content, local requirements, and NOTICES.md before reuse. '
                          'This publication package is a frozen editable source, not a private backup or an ONPF import package.\n').encode('utf-8')
    files['manifest.json'] = _json({'format': 'onpf-publication-package', 'schema_version': 1,
                                   'publication_id': source['publication_id'], 'version': source['publication_version'],
                                   'source_hash': source['source_hash'],
                                   'files': {name: hashlib.sha256(payload).hexdigest() for name, payload in files.items()}})
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, payload)
    return stream.getvalue()
