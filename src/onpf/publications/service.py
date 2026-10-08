"""Public, immutable project copies with explicit privacy review."""
import hashlib
import json
from uuid import uuid4

from onpf.auth.service import require_role
from onpf.additional_documents.service import public_additional
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.exports.projection import public_design
from onpf.materials.service import get_version, list_adoptions
from onpf.programs.framework import load_framework
from onpf.programs.service import get_program
from onpf.permissions.service import permission_readiness


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _approval_status(program_id, revision):
    row = get_db().execute('SELECT 1 FROM releases r JOIN release_candidates c ON c.id=r.candidate_id LEFT JOIN withdrawn_releases w ON w.release_id=r.id WHERE r.program_id=? AND c.program_revision=? AND w.release_id IS NULL LIMIT 1', (program_id, revision)).fetchone()
    return 'approved' if row else 'unapproved'


def preview_source(actor, program_id):
    require_role(actor, program_id, {'owner'})
    from onpf.archives.service import ensure_program_publishable, contains_quarantined_copy
    ensure_program_publishable(program_id)
    program = get_program(actor, program_id)
    materials = [get_version(actor, selection['version_id']) for selection in list_adoptions(actor, program_id)]
    design = public_design(program, load_framework(), materials, public_additional(actor, program_id))
    if contains_quarantined_copy({**design, 'additional_documents': []}):
        raise DomainError('quarantined_content', 'Known removed content is present in this public copy. Review it before publication.', 409)
    return {**design, 'program_id': program_id, 'program_revision': program['revision'],
            'approval_status': _approval_status(program_id, program['revision']),
            'permission_evidence_status': permission_readiness(program_id)['status']}


def publish(actor, program_id: str, expected_revision: int, *, reviewed: bool, selected_additional_ids: list[str] | None = None):
    with transaction() as connection:
        if reviewed is not True:
            raise DomainError('publication_review_required', 'Review every literal public document and material term before publishing.', 422)
        source = preview_source(actor, program_id)
        if type(expected_revision) is not int or expected_revision != source['program_revision']:
            raise DomainError('stale_revision', 'The workspace changed since the public preview. Review it again before publishing.', 409)
        selected = [] if selected_additional_ids is None else selected_additional_ids
        offered = {item['id']: item for item in source['additional_documents']}
        if (not isinstance(selected, list) or len(selected) > 500
                or any(not isinstance(key, str) or key not in offered for key in selected)
                or len(selected) != len(set(selected))):
            raise DomainError('invalid_selection', 'Choose only the media entries offered in this publication review.', 422)
        source['additional_documents'] = [offered[key] for key in selected]
        from onpf.archives.service import contains_quarantined_copy
        if contains_quarantined_copy(source):
            raise DomainError('quarantined_content', 'Known removed content is present in a selected public item. Review it before publication.', 409)
        number = connection.execute('SELECT COALESCE(MAX(version_number),0)+1 FROM publications WHERE program_id=?', (program_id,)).fetchone()[0]
        publication_id, now = str(uuid4()), utcnow()
        source.update({'format': 'onpf-publication', 'schema_version': 1,
                       'publication_id': publication_id, 'publication_version': number, 'published_at': now})
        encoded = _encode(source)
        if len(encoded) > 32 * 1024 * 1024:
            raise DomainError('oversize_source', 'The public project source exceeds the supported size.', 422)
        digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        connection.execute('INSERT INTO publications(id,program_id,version_number,program_revision,approval_status,source_json,source_hash,published_by,published_at) VALUES (?,?,?,?,?,?,?,?,?)',
                           (publication_id, program_id, number, expected_revision, source['approval_status'], encoded, digest, actor.user_id, now))
        return {'id': publication_id, 'program_id': program_id, 'version_number': number, 'source_hash': digest,
                'approval_status': source['approval_status'], 'published_at': now}


def _visible(row):
    state=get_db().execute('SELECT retired_at,public_from_version FROM programs WHERE id=?',(row['program_id'],)).fetchone()
    if not state or state['retired_at'] or row['version_number']<state['public_from_version']:
        return None
    from onpf.archives.service import contains_quarantined_copy
    source = json.loads(row['source_json'])
    if contains_quarantined_copy(source):
        return None
    if hashlib.sha256(row['source_json'].encode('utf-8')).hexdigest() != row['source_hash']:
        raise DomainError('publication_corrupt', 'This published version failed its integrity check.', 409)
    return {**source, 'source_hash': row['source_hash']}


def get_publication(program_id: str, version_number: int):
    row = get_db().execute('SELECT * FROM publications WHERE program_id=? AND version_number=?', (program_id, version_number)).fetchone()
    if not row:
        raise DomainError('not_found', 'This public project version is unavailable.', 404)
    source = _visible(dict(row))
    if source is None:
        raise DomainError('not_found', 'This public project version is unavailable.', 404)
    return source


def list_publications():
    versions = []
    for row in get_db().execute('SELECT * FROM publications ORDER BY published_at DESC,rowid DESC'):
        visible = _visible(dict(row))
        if visible is not None:
            versions.append(visible)
    latest = {}
    for version in versions:
        latest.setdefault(version['program_id'], version)
    return list(latest.values())


def project_versions(program_id):
    versions = []
    for row in get_db().execute('SELECT * FROM publications WHERE program_id=? ORDER BY version_number DESC', (program_id,)):
        visible = _visible(dict(row))
        if visible is not None:
            versions.append(visible)
    return versions
