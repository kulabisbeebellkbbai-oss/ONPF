"""Private project media, bounded uploads, and explicit public selection."""
import base64
import hashlib
import json
from io import BytesIO
from binascii import Error as Base64Error
from pathlib import PurePath
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from onpf.auth.service import require_role
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.programs.service import _revision, _snapshot
from onpf.additional_documents.templates import TEMPLATES

MAX_UPLOAD = 5 * 1024 * 1024
MIME = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg', '.odt': 'application/vnd.oasis.opendocument.text'}


def _text(value, label, maximum, required=False):
    if (not isinstance(value, str) or len(value) > maximum
            or any(ord(char) < 32 and char not in '\t\n\r' for char in value)
            or (required and not value.strip())):
        raise DomainError('invalid_additional_document', f'Enter valid {label} of at most {maximum} characters.', 422)
    return value.strip() if label in {'title', 'permission basis', 'license'} else value


def _content(payload):
    if not isinstance(payload, dict) or payload.get('template_key') not in TEMPLATES:
        raise DomainError('invalid_template', 'Choose an available additional-document template.', 422)
    template = TEMPLATES[payload['template_key']]
    structured=None
    if payload['template_key']=='supporting':
        from onpf.additional_documents.supporting import validate
        structured=validate(payload.get('structured'))
    sections = payload.get('sections')
    if not isinstance(sections, dict) or set(sections) != {item['key'] for item in template['sections']}:
        raise DomainError('invalid_sections', 'Use every section in the selected media template.', 422)
    selected = payload.get('include_in_public', False)
    if type(selected) is not bool:
        raise DomainError('invalid_selection', 'Choose whether this item may enter public project versions.', 422)
    basis = payload.get('ownership_basis')
    if basis not in {'own_work', 'third_party'}:
        raise DomainError('invalid_rights', 'Choose project-owned or third-party rights.', 422)
    normalized = {'template_key': payload['template_key'],
                  'title': _text(payload.get('title'), 'title', 200, True),
                  'sections': {key: _text(sections[key], key, 20000) for key in sections},
                  'ownership_basis': basis,
                  'permission_basis': _text(payload.get('permission_basis'), 'permission basis', 5000, True),
                  'license': _text(payload.get('license'), 'license', 5000, True),
                  'notices': _text(payload.get('notices', ''), 'notices', 20000),
                  'include_in_public': selected}
    normalized.update(structured=structured,is_template=payload.get('is_template',False),
                      reviewed=payload.get('reviewed',False),
                      template_source_id=payload.get('template_source_id'),
                      template_source_revision=payload.get('template_source_revision'))
    if type(normalized['is_template']) is not bool or type(normalized['reviewed']) is not bool:
        raise DomainError('invalid_structure','Choose template and review status explicitly.',422)
    from onpf.drafting.guards import outstanding
    if normalized['reviewed'] and outstanding(structured or normalized['sections']):
        raise DomainError('unreviewed_sections','Read and manually remove section review statements before marking this material reviewed.',422)
    if basis == 'third_party' and not normalized['notices'].strip():
        raise DomainError('missing_notices', 'Record third-party notices before using this media.', 422)
    return normalized


def _upload(upload, filename):
    if upload is None:
        return None
    if not isinstance(upload, bytes) or not upload or len(upload) > MAX_UPLOAD:
        raise DomainError('invalid_upload', 'Attach a nonempty file of at most 5 MiB.', 422)
    if (not isinstance(filename, str) or not filename or len(filename) > 180
            or filename != PurePath(filename).name or '/' in filename or '\\' in filename
            or any(ord(char) < 32 or ord(char) == 127 for char in filename)):
        raise DomainError('invalid_upload', 'Use a simple file name for the attachment.', 422)
    extension = '.' + filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    mimetype = MIME.get(extension)
    if mimetype is None:
        raise DomainError('invalid_upload', 'Use a PDF, ODT, PNG, or JPEG attachment.', 422)
    valid = (upload.startswith(b'%PDF-') if extension == '.pdf' else
             upload.startswith(b'\x89PNG\r\n\x1a\n') if extension == '.png' else
             upload.startswith(b'\xff\xd8\xff') if extension in {'.jpg', '.jpeg'} else False)
    if extension == '.odt':
        try:
            with ZipFile(BytesIO(upload)) as archive:
                info = archive.getinfo('mimetype')
                valid = info.file_size < 100 and archive.read('mimetype') == b'application/vnd.oasis.opendocument.text'
        except (BadZipFile, KeyError, OSError):
            valid = False
    if not valid:
        raise DomainError('invalid_upload', 'The attachment content does not match its supported file type.', 422)
    return {'upload_name': filename, 'upload_mime': mimetype,
            'upload_data': base64.b64encode(upload).decode('ascii'),
            'upload_sha256': hashlib.sha256(upload).hexdigest()}


def _record(row):
    result = dict(row)
    result['sections'] = json.loads(result.pop('sections_json'))
    result['include_in_public'] = bool(result['include_in_public'])
    result['structured']=json.loads(result['structured_json']) if result.get('structured_json') else None
    result['is_template']=bool(result['is_template'])
    result['reviewed']=bool(result['reviewed'])
    return result


def list_additional(actor, program_id):
    require_role(actor, program_id, {'owner', 'facilitator', 'viewer'})
    return [_record(row) for row in get_db().execute(
        'SELECT * FROM additional_documents WHERE program_id=? ORDER BY created_at,id', (program_id,))]


def get_additional(actor, program_id, document_id):
    require_role(actor, program_id, {'owner', 'facilitator', 'viewer'})
    row = get_db().execute('SELECT * FROM additional_documents WHERE id=? AND program_id=?',
                           (document_id, program_id)).fetchone()
    if row is None:
        raise DomainError('not_found', 'This additional document is unavailable.', 404)
    return _record(row)


def save_additional(actor, program_id, payload, expected_revision, document_id=None, *, upload=None, filename=None, remove_upload=False, ai_receipt=None):
    with transaction() as connection:
        require_role(actor, program_id, {'owner', 'facilitator'})
        from onpf.programs.retirement import ensure_editable
        ensure_editable(program_id)
        _revision(_snapshot(program_id), expected_revision)
        values = _content(payload)
        from onpf.drafting import provenance
        verified=provenance._prepare_save(actor,program_id,'supporting',ai_receipt,record_key=document_id)
        attached = _upload(upload, filename)
        prior = None
        if document_id is not None:
            row = connection.execute('SELECT * FROM additional_documents WHERE id=? AND program_id=?',
                                     (document_id, program_id)).fetchone()
            if row is None:
                raise DomainError('not_found', 'This additional document is unavailable.', 404)
            prior = dict(row)
            connection.execute('INSERT OR IGNORE INTO additional_document_revisions VALUES (?,?,?,?,?)',
                               (document_id,prior['revision'],json.dumps(prior,ensure_ascii=False),actor.user_id,prior['updated_at']))
            if values['template_key'] != prior['template_key']:
                raise DomainError('invalid_template', 'An existing document keeps its template.', 422)
        if attached is None:
            attached = {key: None if remove_upload else prior[key] if prior else None
                        for key in ('upload_name', 'upload_mime', 'upload_data', 'upload_sha256')}
        now = utcnow()
        from onpf.drafting.usage import limits
        if values['structured']:
            if attached['upload_data'] and attached['upload_mime'] in {'image/png','image/jpeg'}:
                from PIL import Image, UnidentifiedImageError
                try:
                    with Image.open(BytesIO(base64.b64decode(attached['upload_data']))) as image:
                        if image.width*image.height>16000000 or image.format not in {'PNG','JPEG'}:
                            raise ValueError()
                        image.verify()
                except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):
                    raise DomainError('invalid_image','Use a valid local PNG or JPEG image of at most 16 million pixels.',422) from None
            if any(block['type']=='image' for block in values['structured']['blocks']) and (not attached['upload_data'] or attached['upload_mime'] not in {'image/png','image/jpeg'}):
                raise DomainError('invalid_image','Attach a validated local PNG or JPEG for image blocks.',422)
            from onpf.additional_documents.pdf import render
            render(public_item({**values,**attached,'id':document_id or 'preview'}),page_limit=limits(actor,program_id)['pages'])
        if document_id is None:
            document_id = str(uuid4())
            connection.execute('''INSERT INTO additional_documents
                (id,program_id,template_key,title,sections_json,ownership_basis,permission_basis,license,notices,
                 include_in_public,upload_name,upload_mime,upload_data,upload_sha256,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (document_id, program_id, values['template_key'], values['title'], json.dumps(values['sections'], ensure_ascii=False),
                 values['ownership_basis'], values['permission_basis'], values['license'], values['notices'], int(values['include_in_public']),
                 attached['upload_name'], attached['upload_mime'], attached['upload_data'], attached['upload_sha256'], now, now))
        else:
            connection.execute('''UPDATE additional_documents SET title=?,sections_json=?,ownership_basis=?,permission_basis=?,
                license=?,notices=?,include_in_public=?,upload_name=?,upload_mime=?,upload_data=?,upload_sha256=?,updated_at=?
                WHERE id=? AND program_id=?''',
                (values['title'], json.dumps(values['sections'], ensure_ascii=False), values['ownership_basis'], values['permission_basis'],
                 values['license'], values['notices'], int(values['include_in_public']), attached['upload_name'], attached['upload_mime'],
                 attached['upload_data'], attached['upload_sha256'], now, document_id, program_id))
        revision=prior['revision']+1 if prior else 1
        if prior and (values['template_source_id'] != prior['template_source_id'] or values['template_source_revision'] != prior['template_source_revision']):
            raise DomainError('invalid_template','Existing derived items keep their exact source template revision.',422)
        if values['template_source_id'] and not prior:
            source=get_additional(actor,program_id,values['template_source_id'])
            if source['revision']!=values['template_source_revision'] or not source['reviewed']:
                raise DomainError('template_review_required','Use an accessible reviewed template revision.',422)
        connection.execute('UPDATE additional_documents SET structured_json=?,revision=?,is_template=?,reviewed=?,template_source_id=?,template_source_revision=? WHERE id=?',
                           (json.dumps(values['structured'],ensure_ascii=False) if values['structured'] else None,revision,int(values['is_template']),int(values['reviewed']),values['template_source_id'],values['template_source_revision'],document_id))
        connection.execute('INSERT INTO additional_document_revisions VALUES (?,?,?,?,?)',
                           (document_id,revision,json.dumps(dict(connection.execute('SELECT * FROM additional_documents WHERE id=?',(document_id,)).fetchone()),ensure_ascii=False),actor.user_id,now))
        _check_storage(actor,program_id,document_id,connection)
        connection.execute('UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?', (now, program_id))
        saved=get_additional(actor,program_id,document_id)
        if verified:
            provenance._attach_verified(actor,program_id,{'kind':'supporting','record_key':document_id},verified,{'structured':saved['structured'],'title':saved['title']})
        return saved


def public_additional(actor, program_id):
    """Select literal public fields after an owner has obtained the project preview."""
    return [public_item(item) for item in list_additional(actor, program_id) if item['include_in_public']]


def public_item(item):
    template = TEMPLATES[item['template_key']]
    from onpf.additional_documents.supporting import sections
    return {'id': item['id'], 'template_key': item['template_key'], 'title': item['title'],
            'sections': sections(item['structured']) if item.get('structured') else [{'key': section['key'], 'label': section['label'], 'text': item['sections'][section['key']]}
                         for section in template['sections']],
            'structured':({key:item['structured'][key] for key in ('version','layout','blocks')} if item.get('structured') else None),'revision':item.get('revision',1),
            'template_source_id':item.get('template_source_id'),'template_source_revision':item.get('template_source_revision'),
            'ownership_basis': item['ownership_basis'], 'permission_basis': item['permission_basis'],
            'license': item['license'], 'notices': item['notices'],
            'attachment': ({'name': item['upload_name'], 'mime': item['upload_mime'],
                            'sha256': item['upload_sha256'], 'data': item['upload_data']}
                           if item['upload_data'] else None)}


def _check_storage(actor,program_id,document_id,connection):
    """Count exact current and retained bytes for each scope under the write lock.

    A user's allowance covers materials they originally created, including all
    later revisions by collaborators. Imported legacy items without a creator
    snapshot count at system and project scopes.
    """
    from onpf.drafting.usage import DEFAULT_LIMITS,policies
    records=[dict(row) for row in connection.execute('SELECT * FROM additional_documents')]
    histories=[dict(row) for row in connection.execute('SELECT * FROM additional_document_revisions')]
    creators={row['document_id']:row['saved_by'] for row in histories if row['revision']==1}
    sizes={row['id']:len(json.dumps(row,ensure_ascii=False).encode('utf8')) for row in records}
    for row in histories:
        sizes[row['document_id']]=sizes.get(row['document_id'],0)+len(row['snapshot_json'].encode('utf8'))
    system_limit=DEFAULT_LIMITS['storage_bytes']
    for scope,key,policy in policies(actor,program_id):
        if scope=='user':
            key=creators.get(document_id,actor.user_id)
            row=connection.execute("SELECT limits_json FROM ai_policies WHERE scope='user' AND target_id=?",(key,)).fetchone()
            policy=json.loads(row['limits_json']) if row else {}
        if scope=='system':
            system_limit=policy.get('storage_bytes',system_limit)
        allowance=min(system_limit,policy.get('storage_bytes',system_limit))
        included=[row['id'] for row in records if scope=='system' or
                  scope=='project' and row['program_id']==key or
                  scope=='user' and creators.get(row['id'])==key]
        if sum(sizes[document_id] for document_id in included)>allowance:
            raise DomainError('material_storage_limit',f'The {scope} material storage allowance is reached. Your existing documents remain available.',422)


def attachment_bytes(attachment):
    if not attachment:
        raise DomainError('not_found', 'This attachment is unavailable.', 404)
    try:
        payload = base64.b64decode(attachment['data'], validate=True)
    except (ValueError, Base64Error, KeyError, TypeError):
        raise DomainError('invalid_attachment', 'This attachment failed its integrity check.', 409)
    if hashlib.sha256(payload).hexdigest() != attachment['sha256']:
        raise DomainError('invalid_attachment', 'This attachment failed its integrity check.', 409)
    return payload
