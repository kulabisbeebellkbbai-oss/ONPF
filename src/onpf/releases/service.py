"""Owners approve frozen content; late input belongs to the next design cycle."""
import hashlib
import json
from uuid import uuid4

from onpf.auth.service import require_role
from onpf.db import Record, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.materials.service import get_version, list_adoptions
from onpf.programs.framework import load_framework
from onpf.programs.service import EDIT_ROLES, get_program
from onpf.refinement.service import coverage, list_decisions
from onpf.inquiries.service import clarification_overview

LIFECYCLE_PHRASES = ('working draft', 'not an approval or release', 'documents are working drafts')


def lifecycle_warnings(program):
    """Flag draft-stage claims in public document text for human review."""
    framework = load_framework()
    warnings = []
    for key, content in program['documents'].items():
        sections = {section['key']: section['label'] for section in framework['documents'][key]['sections']}
        for section_key, value in content['sections'].items():
            if any(phrase in value.lower() for phrase in LIFECYCLE_PHRASES):
                warnings.append(f"{framework['documents'][key]['title']} / {sections[section_key]} contains draft-stage wording.")
    return warnings


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(snapshot):
    return hashlib.sha256(_encode(snapshot).encode('utf-8')).hexdigest()


def _row(table, record_id):
    # Fixed internal table names only.
    if not isinstance(record_id, str) or not record_id:
        raise DomainError('invalid_reference', 'Choose a valid release reference.', 422)
    row = get_db().execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
    if row is None:
        raise DomainError('not_found', 'This candidate or release is unavailable.', 404)
    return dict(row)


def _approvals(candidate_id):
    return [dict(row) for row in get_db().execute('SELECT user_id,content_hash,approved_at FROM candidate_approvals WHERE candidate_id=? ORDER BY approved_at,user_id', (candidate_id,))]


def _coverage_snapshot(rows):
    # Private evidence is frozen for audit; no raw contributor text/identity is copied.
    return [{'response_id': row['id'], 'response_revision_id': row['current_revision_id'],
             'status': row['status'], 'reason': row['reason'],
             'disposition_id': row['disposition']['id'] if row['disposition'] else None,
             'proposal_ids': row['proposal_ids'], 'duplicate_of': row['duplicate_of']} for row in rows]


def prepare_candidate(actor, program_id: str, expected_revision: int, *, change_notes: str = '', permission_reviewed: bool = False, clarifications_reviewed: bool = False) -> Record:
    with transaction() as connection:
        require_role(actor, program_id, {'owner'})
        from onpf.archives.service import ensure_program_publishable, select_candidate_decisions
        ensure_program_publishable(program_id)
        program = get_program(actor, program_id)
        from onpf.drafting.guards import require_reviewed
        require_reviewed(program['documents'])
        from onpf.permissions.service import permission_readiness, review_required
        permission_status = permission_readiness(program_id)["status"]
        if review_required(program) and permission_reviewed is not True:
            raise DomainError('permission_review_required', 'Review missing or unresolved operating-permission evidence and acknowledge it before preparing this design candidate.', 422)
        if type(expected_revision) is not int or program['revision'] != expected_revision:
            raise DomainError('stale_revision', 'The program changed. Review the current draft before preparing a candidate; your notes are preserved.', 409)
        if not isinstance(change_notes, str) or len(change_notes) > 5000 or '\x00' in change_notes:
            raise DomainError('invalid_notes', 'Enter change notes of at most 5000 characters.', 422)
        scoped = coverage(actor, program_id)
        if any(row['status'] == 'unreviewed' for row in scoped):
            raise DomainError('unreviewed_input', 'Review every current response before preparing a candidate. Explained deferrals are allowed.', 422)
        rounds = clarification_overview(actor, program_id)
        if any(item['pending'] for item in rounds):
            raise DomainError('pending_clarification', 'Answer or explicitly defer every pending clarification question before preparing a candidate.', 422)
        if any(item['deferred'] for item in rounds) and clarifications_reviewed is not True:
            raise DomainError('clarification_review_required', 'Review and acknowledge the deferred clarification questions before preparing a candidate.', 422)
        materials = [get_version(actor, selection['version_id']) for selection in list_adoptions(actor, program_id)]
        snapshot = {key: program[key] for key in ('id', 'title', 'purpose', 'local_context', 'operating_status', 'module_keys', 'documents', 'document_decision_ids')}
        snapshot.pop('id')
        snapshot.update({'schema_version': 1, 'program_id': program_id, 'program_revision': expected_revision,
                         'owner_ids': sorted(program['owner_ids']), 'approval_rule': program['approval_rule'],
                         'owner_labels': {member['user_id']: member['username'] for member in program['memberships'] if member['role'] == 'owner'},
                         'response_revision_ids': [row['current_revision_id'] for row in scoped],
                         'coverage': _coverage_snapshot(scoped), 'materials': materials,
                         'permission_evidence_status': permission_status,
                         'clarification_rounds': rounds, 'clarifications_reviewed': clarifications_reviewed,
                         'lifecycle_warnings': lifecycle_warnings(program),
                         'material_version_ids': [version['id'] for version in materials],
                         'framework': load_framework(), 'decisions': select_candidate_decisions(program_id, list_decisions(actor, program_id)),
                         'change_notes': change_notes})
        candidate_id, now = str(uuid4()), utcnow()
        from onpf.archives.service import quarantined
        if quarantined('release_candidates', {'id': candidate_id, 'snapshot': _encode(snapshot)}, program_id):
            raise DomainError('quarantined_content', 'Known copied personal content needs review before preparing another candidate.', 409)
        connection.execute('INSERT INTO release_candidates(id,program_id,program_revision,content_hash,snapshot,prepared_by,prepared_at) VALUES (?,?,?,?,?,?,?)', (candidate_id, program_id, expected_revision, _hash(snapshot), _encode(snapshot), actor.user_id, now))
        connection.executemany('INSERT INTO candidate_owners(candidate_id,user_id) VALUES (?,?)', [(candidate_id, user_id) for user_id in snapshot['owner_ids']])
        return get_candidate(actor, candidate_id)


def _validate_current(actor, row, snapshot, current_coverage=None):
    from onpf.archives.service import ensure_program_publishable, quarantined
    ensure_program_publishable(row['program_id'])
    if quarantined('release_candidates', row, row['program_id']):
        raise DomainError('quarantined_content', 'This candidate contains removed input. Prepare reviewed replacement content.', 409)
    program = get_program(actor, row['program_id'])
    owners = [owner['user_id'] for owner in get_db().execute('SELECT user_id FROM candidate_owners WHERE candidate_id=? ORDER BY user_id', (row['id'],))]
    if (_hash(snapshot) != row['content_hash'] or snapshot['program_revision'] != row['program_revision']
            or snapshot['program_id'] != row['program_id'] or program['revision'] != row['program_revision']
            or sorted(program['owner_ids']) != snapshot['owner_ids'] or owners != snapshot['owner_ids']
            or program['approval_rule'] != snapshot['approval_rule']):
        raise DomainError('stale_candidate', 'The design, owners, or approval rule changed. Prepare a new candidate and collect fresh approvals.', 409)
    if 'clarification_rounds' in snapshot and clarification_overview(actor, row['program_id']) != snapshot['clarification_rounds']:
        raise DomainError('stale_candidate', 'Clarification progress changed. Review it and prepare a new candidate.', 409)
    current = {response['id']: response for response in (current_coverage if current_coverage is not None else coverage(actor, row['program_id']))}
    for frozen in snapshot['coverage']:
        response = current.get(frozen['response_id'])
        if response is None or response['current_revision_id'] != frozen['response_revision_id'] or response['status'] == 'unreviewed':
            raise DomainError('stale_candidate', 'A scoped response changed or needs review. Prepare a new candidate and collect fresh approvals.', 409)
    if any(approval['content_hash'] != row['content_hash'] for approval in _approvals(row['id'])):
        raise DomainError('stale_candidate', 'An approval does not match this exact candidate.', 409)


def get_candidate(actor, candidate_id: str) -> Record:
    row = _row('release_candidates', candidate_id)
    require_role(actor, row['program_id'], EDIT_ROLES)
    snapshot = json.loads(row['snapshot'])
    release = get_db().execute('SELECT id FROM releases WHERE candidate_id=?', (candidate_id,)).fetchone()
    current = coverage(actor, row['program_id'])
    frozen = {item['response_id']: item['response_revision_id'] for item in snapshot['coverage']}
    pending = sum(frozen.get(item['id']) != item['current_revision_id'] for item in current)
    stale = False
    if not release:
        try:
            _validate_current(actor, row, snapshot, current)
        except DomainError as error:
            if error.status != 409:
                raise
            stale = True
    return {**snapshot, 'id': candidate_id, 'content_hash': row['content_hash'], 'prepared_by': row['prepared_by'],
            'prepared_at': row['prepared_at'], 'approvals': _approvals(candidate_id),
            'state': 'released' if release else 'pending', 'release_id': release['id'] if release else None,
            'pending_input_count': pending, 'stale': stale}


def list_candidates(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [get_candidate(actor, row['id']) for row in get_db().execute('SELECT id FROM release_candidates WHERE program_id=? ORDER BY prepared_at DESC,rowid DESC', (program_id,))]


def approve_candidate(actor, candidate_id: str, *, permission_reviewed: bool = False) -> Record:
    with transaction() as connection:
        row = _row('release_candidates', candidate_id)
        require_role(actor, row['program_id'], {'owner'})
        snapshot = json.loads(row['snapshot'])
        from onpf.drafting.guards import require_reviewed
        require_reviewed(snapshot['documents'])
        if actor.user_id not in snapshot['owner_ids']:
            raise DomainError('forbidden', 'Only the fixed decision owners may approve this candidate.', 403)
        existing = connection.execute('SELECT id FROM releases WHERE candidate_id=?', (candidate_id,)).fetchone()
        if existing:
            return {'state': 'released', 'release_id': existing['id']}
        # All checks, approval append and release creation share this serialized transaction.
        _validate_current(actor, row, snapshot)
        from onpf.permissions.service import review_required
        if review_required(get_program(actor, row['program_id'])) and permission_reviewed is not True:
            raise DomainError('permission_review_required', 'Review unresolved operating-permission evidence and acknowledge it before approving this design candidate.', 422)
        connection.execute('INSERT INTO candidate_approvals(candidate_id,user_id,content_hash,approved_at) VALUES (?,?,?,?) ON CONFLICT(candidate_id,user_id) DO NOTHING', (candidate_id, actor.user_id, row['content_hash'], utcnow()))
        approvals = _approvals(candidate_id)
        complete = snapshot['approval_rule'] == 'any' or set(a['user_id'] for a in approvals) == set(snapshot['owner_ids'])
        if not complete:
            return {'state': 'pending', 'release_id': None}
        release_id = str(uuid4())
        number = connection.execute('SELECT COALESCE(MAX(release_number),0)+1 FROM releases WHERE program_id=?', (row['program_id'],)).fetchone()[0]
        connection.execute('INSERT INTO releases(id,program_id,candidate_id,release_number,content_hash,snapshot,approvals,released_at) VALUES (?,?,?,?,?,?,?,?)', (release_id, row['program_id'], candidate_id, number, row['content_hash'], row['snapshot'], _encode(approvals), utcnow()))
        return {'state': 'released', 'release_id': release_id}


def get_release(actor, release_id: str) -> Record:
    row = _row('releases', release_id)
    require_role(actor, row['program_id'], EDIT_ROLES)
    from onpf.archives.service import release_available
    snapshot = json.loads(row['snapshot'])
    return {**json.loads(row['snapshot']), 'id': row['id'], 'candidate_id': row['candidate_id'],
            'release_number': row['release_number'], 'content_hash': row['content_hash'],
            'approvals': json.loads(row['approvals']), 'released_at': row['released_at'],
            'state': 'released' if release_available(release_id, snapshot) else 'withdrawn'}


def list_releases(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [get_release(actor, row['id']) for row in get_db().execute('SELECT id FROM releases WHERE program_id=? ORDER BY release_number DESC', (program_id,))]


def drafting_lifecycle(actor, program_id: str) -> dict:
    """Authorized, status-only advisory projection; never copy frozen text.

    Withdrawn/quarantined records contribute their status and exact hashes only.
    They do not grant access to any immutable source content. This projection is
    ephemeral: mutable status has no historical witness suitable for archives.
    """
    from onpf.archives.service import quarantined
    program = get_program(actor, program_id)
    draft_keys = ('title', 'purpose', 'local_context', 'operating_status', 'module_keys',
                  'documents', 'document_decision_ids')
    current_draft_hash = _hash({key: program[key] for key in draft_keys})
    current_scope = {'owner_ids': sorted(program['owner_ids']), 'approval_rule': program['approval_rule']}
    records = []
    for candidate in list_candidates(actor, program_id):
        row = _row('release_candidates', candidate['id'])
        candidate_available = not quarantined('release_candidates', row, program_id)
        approvals = candidate['approvals']
        matching = [a for a in approvals if a['content_hash'] == candidate['content_hash']
                    and a['user_id'] in candidate['owner_ids']]
        release = get_release(actor, candidate['release_id']) if candidate['release_id'] else None
        records.append({
            'candidate_id': candidate['id'], 'candidate_hash': candidate['content_hash'],
            'prepared_revision': candidate['program_revision'],
            'approval_rule': candidate['approval_rule'],
            'required_approvals': len(candidate['owner_ids']) if candidate['approval_rule'] == 'all' else 1,
            'matching_approvals': len(matching), 'approval_hashes_match': len(matching) == len(approvals),
            'state': release['state'] if release else 'pending',
            'candidate_stale': candidate['stale'], 'candidate_available': candidate_available,
            'current_draft_matches': current_draft_hash == _hash({key: candidate[key] for key in draft_keys}),
            'current_approval_scope_matches': current_scope == {'owner_ids': candidate['owner_ids'], 'approval_rule': candidate['approval_rule']},
            'pending_input_count': candidate['pending_input_count'],
            'release': {'id': release['id'], 'number': release['release_number'],
                        'content_hash': release['content_hash'],
                        'matches_candidate': release['content_hash'] == candidate['content_hash']}
                       if release else None,
        })
    return {'scope': 'Program-wide recorded design lifecycle; design approval does not confirm operating permission.',
            'operating_status': program['operating_status'], 'current_draft_hash': current_draft_hash,
            'current_approval_scope_hash': _hash(current_scope), 'candidates': records}


def material_from_release(actor, release_id: str) -> Record:
    from onpf.materials.service import material_from_release as share
    return share(actor, release_id)
