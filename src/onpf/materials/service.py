"""Private material drafts and immutable, explicitly selected reusable versions."""
import difflib
import hashlib
import json
from uuid import uuid4

from onpf.auth.models import Principal
from onpf.auth.service import require_role
from onpf.db import Record, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.programs.service import EDIT_ROLES, get_program


def _text(value, label, maximum=50000, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (required and not value.strip()):
        raise DomainError('invalid_material', f'Enter {label} as text of at most {maximum} characters.', 422)
    return value


def _record(value, allowed, required):
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise DomainError('invalid_content', 'Use the labeled editable material content fields only.', 422)


def validate_content(value):
    """Allowlist editable text; private release, decision and identity fields are rejected."""
    _record(value, {'schema_version', 'kind', 'text', 'documents'}, {'schema_version', 'kind'})
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise DomainError('invalid_content', 'This material content schema is unsupported.', 422)
    if value['kind'] == 'text':
        _record(value, {'schema_version', 'kind', 'text'}, {'schema_version', 'kind', 'text'})
        _text(value['text'], 'material text', required=True)
    elif value['kind'] == 'document_bundle':
        _record(value, {'schema_version', 'kind', 'documents'}, {'schema_version', 'kind', 'documents'})
        documents = value['documents']
        if not isinstance(documents, dict) or not documents or len(documents) > 50:
            raise DomainError('invalid_content', 'Include one to fifty editable documents.', 422)
        for key, document in documents.items():
            _text(key, 'document key', 100, True)
            _record(document, {'title', 'sections', 'rows'}, {'title', 'sections'})
            _text(document['title'], 'document title', 200, True)
            if not isinstance(document['sections'], list) or len(document['sections']) > 100:
                raise DomainError('invalid_content', 'Include up to one hundred labeled sections per document.', 422)
            section_keys = set()
            for section in document['sections']:
                _record(section, {'key', 'label', 'text'}, {'key', 'label', 'text'})
                for field, limit in [('key', 100), ('label', 200), ('text', 50000)]:
                    _text(section[field], f'section {field}', limit, field != 'text')
                if section['key'] in section_keys:
                    raise DomainError('invalid_content', 'Section keys must be unique within each document.', 422)
                section_keys.add(section['key'])
            if 'rows' in document:
                if not isinstance(document['rows'], list) or len(document['rows']) > 500:
                    raise DomainError('invalid_content', 'Include up to five hundred budget rows.', 422)
                for row in document['rows']:
                    fields = {'item', 'quantity', 'unit_cost', 'cost_status', 'notes'}
                    _record(row, fields, fields)
                    for field, limit in [('item', 1000), ('quantity', 100), ('notes', 5000)]:
                        _text(row[field], field, limit)
                    if row['unit_cost'] is not None:
                        _text(row['unit_cost'], 'unit cost', 100)
                    if row['cost_status'] not in ('estimated', 'confirmed'):
                        raise DomainError('invalid_content', 'Label costs estimated or confirmed.', 422)
    else:
        raise DomainError('invalid_content', 'Choose text or a document bundle.', 422)
    encoded = _encode(value)
    if len(encoded.encode('utf-8')) > 2000000:
        raise DomainError('invalid_content', 'Material content must fit within two megabytes.', 422)
    return json.loads(encoded)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _designer(actor):
    if not isinstance(actor, Principal) or not actor.user_id or actor.invite_id or actor.batch_id or not get_db().execute("SELECT 1 FROM memberships WHERE user_id=? AND role IN ('owner','facilitator')", (actor.user_id,)).fetchone():
        raise DomainError('forbidden', 'An authenticated program designer is required.', 403)


def _row(table, record_id):
    # Table names are fixed internal constants, never request values.
    if not isinstance(record_id, str) or not record_id:
        raise DomainError('invalid_reference', 'Choose a valid material reference.', 422)
    row = get_db().execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
    if row is None:
        raise DomainError('not_found', 'This material record is unavailable.', 404)
    return dict(row)


def _snapshot(row):
    return {**row, 'content': json.loads(row['content']), 'source_terms': _source_terms(row['source_version_id']) + _import_terms(row.get('material_id', row['id']))}


def _import_terms(material_id):
    """Validated original public terms remain provenance, never imported authority."""
    row = get_db().execute('SELECT source_json FROM imported_material_sources WHERE material_id=?', (material_id,)).fetchone()
    if row is None:
        return []
    source = json.loads(row['source_json'])
    fields = ('id', 'material_id', 'version_number', 'title', 'ownership_basis', 'permission_basis', 'license', 'notices', 'content_hash')
    return [{key: source[key] for key in fields}] + [{key: term[key] for key in fields} for term in source['source_terms']]


def _source_terms(version_id):
    terms, visited = [], set()
    while version_id:
        if version_id in visited:
            raise DomainError('invalid_lineage', 'The source lineage contains a cycle.', 422)
        visited.add(version_id)
        source = _row('material_versions', version_id)
        terms.append({key: source[key] for key in ('id', 'material_id', 'version_number', 'title', 'ownership_basis', 'permission_basis', 'license', 'notices', 'content_hash')})
        terms.extend(_import_terms(source['material_id']))
        version_id = source['source_version_id']
    return terms


def _revision(current, expected):
    if type(expected) is not int or expected != current['revision']:
        raise DomainError('stale_revision', 'This draft or program changed. Review the current version before saving your preserved work.', 409)


def _bump(program_id):
    get_db().execute('UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?', (utcnow(), program_id))


def get_material(actor, program_id: str, material_id: str) -> Record:
    require_role(actor, program_id, EDIT_ROLES)
    row = _row('materials', material_id)
    if row['program_id'] != program_id:
        raise DomainError('forbidden', 'Choose a draft in this program.', 403)
    return _snapshot(row)


def list_materials(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [_snapshot(dict(row)) for row in get_db().execute('SELECT * FROM materials WHERE program_id=? ORDER BY created_at,rowid', (program_id,))]


def get_version(actor, version_id: str) -> Record:
    _designer(actor)
    snapshot = _shared_snapshot(_row('material_versions', version_id))
    if snapshot is None:
        raise DomainError('quarantined_content', 'This shared version contains removed input and is unavailable for further reuse. Review a replacement.', 409)
    return snapshot


def _shared_snapshot(row):
    from onpf.archives.service import contains_quarantined_copy
    if get_db().execute("SELECT 1 FROM quarantined_content WHERE table_name='material_versions' AND record_key=?", (row['id'],)).fetchone():
        return None
    snapshot = _snapshot(row)
    # Source terms are part of this disclosure even when the adaptation is clean.
    return None if contains_quarantined_copy(snapshot) else snapshot


def list_versions(actor, material_id: str | None = None) -> list[Record]:
    _designer(actor)
    if material_id is not None:
        if not isinstance(material_id, str):
            raise DomainError('invalid_reference', 'Choose a valid material reference.', 422)
        rows = get_db().execute('SELECT * FROM material_versions WHERE material_id=? ORDER BY version_number', (material_id,))
    else:
        rows = get_db().execute('SELECT * FROM material_versions ORDER BY title,version_number')
    return [snapshot for row in rows if (snapshot := _shared_snapshot(dict(row))) is not None]


def _values(payload, current=None):
    _record(payload, {'id', 'title', 'content', 'ownership_basis', 'permission_basis', 'license', 'notices'}, {'title', 'content', 'ownership_basis', 'permission_basis', 'license', 'notices'})
    values = {key: payload[key] for key in ('title', 'ownership_basis', 'permission_basis', 'license', 'notices')}
    for key, limit in [('title', 200), ('permission_basis', 5000), ('license', 5000), ('notices', 50000)]:
        _text(values[key], key.replace('_', ' '), limit, key != 'notices')
    if values['ownership_basis'] not in ('own_work', 'third_party'):
        raise DomainError('invalid_permission', 'Record own work or third-party material and its permission basis.', 422)
    values['content'] = validate_content(payload['content'])
    if current and current['source_version_id']:
        source = _row('material_versions', current['source_version_id'])
        if source['notices'] not in values['notices']:
            raise DomainError('source_terms', 'Retain the inherited source notices verbatim in this adaptation. Original permission terms remain in the source version.', 422)
    if current:
        for term in _import_terms(current['id']):
            if term['notices'] not in values['notices']:
                raise DomainError('source_terms', 'Retain imported source notices verbatim. Original public terms remain in the import provenance.', 422)
    return values


def _insert(actor, program_id, values, source_version_id=None):
    material_id, now = str(uuid4()), utcnow()
    get_db().execute('INSERT INTO materials(id,program_id,title,content,ownership_basis,permission_basis,license,notices,source_version_id,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (material_id, program_id, values['title'], _encode(values['content']), values['ownership_basis'], values['permission_basis'], values['license'], values['notices'], source_version_id, actor.user_id, now, now))
    _bump(program_id)
    return get_material(actor, program_id, material_id)


def save_material(actor, program_id: str, payload: Record, expected_revision: int | None) -> Record:
    with transaction() as connection:
        require_role(actor, program_id, EDIT_ROLES)
        if not isinstance(payload, dict):
            raise DomainError('invalid_material', 'Enter the labeled material fields.', 422)
        material_id = payload.get('id')
        current = get_material(actor, program_id, material_id) if material_id is not None else None
        if current:
            _revision(current, expected_revision)
        elif expected_revision is not None:
            raise DomainError('stale_revision', 'New drafts have no existing revision.', 409)
        values = _values(payload, current)
        if current is None:
            return _insert(actor, program_id, values)
        connection.execute('UPDATE materials SET title=?,content=?,ownership_basis=?,permission_basis=?,license=?,notices=?,revision=revision+1,updated_at=? WHERE id=?', (values['title'], _encode(values['content']), values['ownership_basis'], values['permission_basis'], values['license'], values['notices'], utcnow(), material_id))
        _bump(program_id)
        return get_material(actor, program_id, material_id)


def release_material(actor, material_id: str, expected_revision: int) -> Record:
    with transaction() as connection:
        row = _row('materials', material_id)
        require_role(actor, row['program_id'], {'owner'})
        _revision(row, expected_revision)
        from onpf.archives.service import quarantined
        if quarantined('materials', row, row['program_id']):
            raise DomainError('quarantined_content', 'Review and remove known copied personal content before releasing a replacement material.', 409)
        # Repeating release of the identical draft is a safe idempotent read.
        existing = connection.execute('SELECT id FROM material_versions WHERE material_id=? AND material_revision=?', (material_id, expected_revision)).fetchone()
        if existing:
            return get_version(actor, existing['id'])
        version_id, now = str(uuid4()), utcnow()
        number = connection.execute('SELECT COALESCE(MAX(version_number),0)+1 FROM material_versions WHERE material_id=?', (material_id,)).fetchone()[0]
        connection.execute('INSERT INTO material_versions(id,material_id,version_number,material_revision,title,content,ownership_basis,permission_basis,license,notices,source_version_id,content_hash,released_by,released_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (version_id, material_id, number, expected_revision, row['title'], row['content'], row['ownership_basis'], row['permission_basis'], row['license'], row['notices'], row['source_version_id'], hashlib.sha256(row['content'].encode('utf-8')).hexdigest(), actor.user_id, now))
        return get_version(actor, version_id)


def list_adoptions(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [_adoption_snapshot(row) for row in get_db().execute('SELECT * FROM program_materials WHERE program_id=? ORDER BY adopted_at,material_id', (program_id,))]


def _adoption_snapshot(row):
    version = _shared_snapshot(_row('material_versions', row['version_id']))
    # Unavailable selections expose identifiers/status only, never copied titles/terms.
    return {**dict(row), 'available': version is not None, 'version': version}


def adopt_material(actor, program_id: str, version_id: str, expected_revision: int) -> Record:
    with transaction() as connection:
        require_role(actor, program_id, {'owner'})
        _revision(get_program(actor, program_id), expected_revision)
        version, now = get_version(actor, version_id), utcnow()
        connection.execute('INSERT INTO program_materials(program_id,material_id,version_id,adopted_by,adopted_at) VALUES (?,?,?,?,?) ON CONFLICT(program_id,material_id) DO UPDATE SET version_id=excluded.version_id,adopted_by=excluded.adopted_by,adopted_at=excluded.adopted_at', (program_id, version['material_id'], version_id, actor.user_id, now))
        _bump(program_id)
        return _adoption_snapshot(connection.execute('SELECT * FROM program_materials WHERE program_id=? AND material_id=?', (program_id, version['material_id'])).fetchone())


def remove_adoption(actor, program_id: str, material_id: str, expected_revision: int) -> None:
    with transaction() as connection:
        require_role(actor, program_id, {'owner'})
        _revision(get_program(actor, program_id), expected_revision)
        removed = connection.execute('DELETE FROM program_materials WHERE program_id=? AND material_id=?', (program_id, material_id))
        if not removed.rowcount:
            raise DomainError('not_found', 'Choose a current material selection in this program.', 404)
        _bump(program_id)


def derive_material(actor, program_id: str, source_version_id: str) -> Record:
    with transaction():
        require_role(actor, program_id, EDIT_ROLES)
        version = get_version(actor, source_version_id)
        return _insert(actor, program_id, version, source_version_id)


def propose_improvement(actor, program_id: str, derivative_id: str, note: str, expected_revision: int, *, share_content: bool = False) -> Record:
    with transaction() as connection:
        derivative = get_material(actor, program_id, derivative_id)
        _revision(derivative, expected_revision)
        _text(note, 'the improvement note to share with source owners', 5000, True)
        if type(share_content) is not bool or not derivative['source_version_id']:
            raise DomainError('invalid_improvement', 'Choose a local derivative and whether to include its current content.', 422)
        source = _row('material_versions', derivative['source_version_id'])
        source_program = _row('materials', source['material_id'])['program_id']
        proposal_id, now = str(uuid4()), utcnow()
        content = _encode(derivative['content']) if share_content else None
        connection.execute('INSERT INTO material_improvement_proposals(id,source_program_id,source_version_id,derivative_program_id,derivative_id,derivative_revision,note,proposed_content,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (proposal_id, source_program, source['id'], program_id, derivative_id, derivative['revision'], note, content, actor.user_id, now))
        return {'id': proposal_id, 'source_program_id': source_program, 'source_version_id': source['id'], 'derivative_id': derivative_id, 'derivative_revision': derivative['revision'], 'note': note, 'proposed_content': json.loads(content) if content else None, 'created_at': now}


def list_improvements(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, {'owner'})
    # Only intentionally submitted notes and frozen content are exposed to source owners.
    return [{**dict(row), 'proposed_content': json.loads(row['proposed_content']) if row['proposed_content'] else None} for row in get_db().execute('SELECT id,source_version_id,derivative_program_id,derivative_id,derivative_revision,note,proposed_content,created_at FROM material_improvement_proposals WHERE source_program_id=? ORDER BY created_at,rowid', (program_id,))]


def compare_versions(actor, left_version_id: str, right_version_id: str) -> Record:
    left, right = get_version(actor, left_version_id), get_version(actor, right_version_id)
    if left['material_id'] != right['material_id']:
        raise DomainError('invalid_comparison', 'Choose two released versions of the same material.', 422)
    def lines(version):
        values = [f"Title: {version['title']}", f"Ownership basis: {version['ownership_basis'].replace('_', ' ')}", f"Permission basis: {version['permission_basis']}", f"License: {version['license']}", f"Notices: {version['notices']}", f"Source version: {version['source_version_id'] or 'Original material'}"]
        content = version['content']
        if content['kind'] == 'text':
            values.append('Material text:\n' + content['text'])
        else:
            for document in content['documents'].values():
                values.append('Document: ' + document['title'])
                for section in document['sections']:
                    values.append(section['label'] + ':\n' + section['text'])
                for row in document.get('rows', []):
                    values.append('Budget item: ' + '; '.join(f"{key.replace('_', ' ').capitalize()}: {value if value is not None else 'Unknown'}" for key, value in row.items()))
        return '\n'.join(values).splitlines()
    changes = []
    for line in difflib.ndiff(lines(left), lines(right)):
        if line.startswith('- '):
            changes.append('Earlier: ' + line[2:])
        elif line.startswith('+ '):
            changes.append('Later: ' + line[2:])
        elif line.startswith('  '):
            changes.append(line[2:])
    identical = lines(left) == lines(right)
    return {'left': left, 'right': right, 'diff': '' if identical else '\n'.join(changes)}


def material_from_release(actor, release_id: str) -> Record:
    """Share only editable documents, with one stable source per program and one version per release."""
    from onpf.releases.service import get_release
    with transaction() as connection:
        release = get_release(actor, release_id)
        if release['state'] != 'released':
            raise DomainError('unavailable_release', 'This release has been withdrawn from reuse.', 409)
        require_role(actor, release['program_id'], {'owner'})
        existing = connection.execute('SELECT version_id FROM release_material_versions WHERE release_id=?', (release_id,)).fetchone()
        if existing:
            return get_version(actor, existing['version_id'])
        documents = {}
        for key, structure in release['framework']['documents'].items():
            frozen = release['documents'][key]
            document = {'title': structure['title'], 'sections': [
                {'key': section['key'], 'label': section['label'], 'text': frozen['sections'][section['key']]}
                for section in structure['sections']]}
            if 'rows' in frozen:
                document['rows'] = [{field: row[field] for field in ('item', 'quantity', 'unit_cost', 'cost_status', 'notes')} for row in frozen['rows']]
            documents[key] = document
        payload = {'title': release['title'], 'content': {'schema_version': 1, 'kind': 'document_bundle', 'documents': documents},
                   'ownership_basis': 'own_work', 'permission_basis': 'Program decision owners release these editable documents under MIT-0.',
                   'license': 'MIT-0', 'notices': f"Released from {release['title']}, release {release['release_number']}. MIT-0 license."}
        source = connection.execute('SELECT material_id FROM release_material_sources WHERE program_id=?', (release['program_id'],)).fetchone()
        if source:
            current = get_material(actor, release['program_id'], source['material_id'])
            payload['id'] = current['id']
            draft = save_material(actor, release['program_id'], payload, current['revision'])
        else:
            draft = save_material(actor, release['program_id'], payload, None)
            connection.execute('INSERT INTO release_material_sources(program_id,material_id) VALUES (?,?)', (release['program_id'], draft['id']))
        version = release_material(actor, draft['id'], draft['revision'])
        connection.execute('INSERT INTO release_material_versions(release_id,version_id) VALUES (?,?)', (release_id, version['id']))
        return version
