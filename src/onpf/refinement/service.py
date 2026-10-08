"""Transactional design edits; review capabilities expose frozen documents only."""
import json
import secrets
from uuid import uuid4

from onpf.auth.service import require_role, token_hash
from onpf.contributions.service import get_response, list_responses
from onpf.db import Record, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.programs.framework import load_framework
from onpf.programs.service import EDIT_ROLES, get_program

STATES = ('unreviewed', 'incorporated', 'adapted', 'deferred', 'declined')


def _text(value, label, maximum=50000, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (required and not value.strip()):
        raise DomainError('invalid_text', f'Enter {label} as text of at most {maximum} characters.', 422)
    return value


def _ids(values, label):
    if not isinstance(values, list) or len(values) > 500 or any(not isinstance(value, str) or not value for value in values):
        raise DomainError('invalid_links', f'Choose up to 500 valid {label} references.', 422)
    return list(dict.fromkeys(values))


def _row(table, record_id, program_id):
    # Table names are exclusively fixed internal call-site constants.
    if not isinstance(record_id, str):
        raise DomainError('invalid_link', 'Choose a valid record reference.', 422)
    row = get_db().execute(f'SELECT * FROM {table} WHERE id=?', (record_id,)).fetchone()
    if row is None:
        raise DomainError('not_found', 'This linked record is unavailable.', 404)
    if row['program_id'] != program_id:
        raise DomainError('forbidden', 'Links must belong to the same program.', 403)
    return dict(row)


def _responses(actor, program_id, values):
    results = []
    for response_id in _ids(values, 'response'):
        response = get_response(actor, response_id)
        if response['program_id'] != program_id:
            raise DomainError('forbidden', 'Responses must belong to the same program.', 403)
        results.append(response)
    return results


def _proposals(program_id, values):
    return [_row('proposals', record_id, program_id) for record_id in _ids(values, 'proposal')]


def _bump(program_id):
    get_db().execute('UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?', (utcnow(), program_id))


def get_proposal(actor, program_id: str, proposal_id: str) -> Record:
    require_role(actor, program_id, EDIT_ROLES)
    row = _row('proposals', proposal_id, program_id)
    links = [dict(link) for link in get_db().execute('SELECT response_id,response_revision_id FROM proposal_responses WHERE proposal_id=? ORDER BY ordinal', (proposal_id,))]
    return {**row, 'response_ids': [link['response_id'] for link in links], 'response_links': links}


def list_proposals(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [get_proposal(actor, program_id, row['id']) for row in get_db().execute('SELECT id FROM proposals WHERE program_id=? ORDER BY created_at,rowid', (program_id,))]


def save_proposal(actor, program_id: str, payload: Record, expected_revision: int | None, *, ai_receipt: str | None = None) -> Record:
    with transaction() as connection:
        require_role(actor, program_id, EDIT_ROLES)
        if not isinstance(payload, dict):
            raise DomainError('invalid_proposal', 'Enter the labeled proposal fields.', 422)
        title = _text(payload.get('title', ''), 'a proposal title', 200, True)
        text = _text(payload.get('text', ''), 'a proposal', required=True)
        theme = _text(payload.get('theme', ''), 'a theme', 200)
        responses = _responses(actor, program_id, payload.get('response_ids', []))
        proposal_id, now = payload.get('id'), utcnow()
        from onpf.drafting import provenance
        verified = provenance._prepare_save(actor, program_id, 'proposal', ai_receipt, record_key=proposal_id)
        prior_response_ids = set()
        if proposal_id is not None:
            current = _row('proposals', proposal_id, program_id)
            if type(expected_revision) is not int or current['revision'] != expected_revision:
                raise DomainError('stale_revision', 'This proposal changed. Reconcile the current option with your preserved text before saving.', 409)
            prior_response_ids = {row['response_id'] for row in connection.execute('SELECT response_id FROM proposal_responses WHERE proposal_id=?', (proposal_id,))}
            connection.execute('UPDATE proposals SET title=?,text=?,theme=?,revision=revision+1,updated_at=? WHERE id=?', (title, text, theme, now, proposal_id))
            connection.execute('DELETE FROM proposal_responses WHERE proposal_id=?', (proposal_id,))
        else:
            if expected_revision is not None:
                raise DomainError('stale_revision', 'New proposals have no existing revision.', 409)
            proposal_id = str(uuid4())
            connection.execute('INSERT INTO proposals(id,program_id,title,text,theme,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)', (proposal_id, program_id, title, text, theme, actor.user_id, now, now))
        connection.executemany('INSERT INTO proposal_responses(proposal_id,response_id,response_revision_id,ordinal) VALUES (?,?,?,?)', [(proposal_id, row['id'], row['current_revision_id'], index) for index, row in enumerate(responses)])
        connection.executemany('INSERT OR IGNORE INTO proposal_incorporations(proposal_id,response_id,response_revision_id,recorded_by,recorded_at) VALUES (?,?,?,?,?)', [(proposal_id, row['id'], row['current_revision_id'], actor.user_id, now) for row in responses])
        for row in responses:
            connection.execute('UPDATE responses SET review_required=0 WHERE id=? AND revision=?', (row['id'], row['revision']))
        for response_id in prior_response_ids - {row['id'] for row in responses}:
            if not connection.execute('SELECT 1 FROM proposal_responses WHERE response_id=?', (response_id,)).fetchone() and not connection.execute('SELECT 1 FROM dispositions WHERE response_id=?', (response_id,)).fetchone():
                connection.execute('UPDATE responses SET review_required=1 WHERE id=?', (response_id,))
        _bump(program_id)
        if verified:
            provenance._attach_verified(actor, program_id, {'kind': 'proposal', 'record_key': proposal_id}, verified,
                                        get_proposal(actor, program_id, proposal_id))
        return get_proposal(actor, program_id, proposal_id)


def _disposition_snapshot(row):
    return {**dict(row), 'proposal_ids': [link['proposal_id'] for link in get_db().execute('SELECT proposal_id FROM disposition_proposals WHERE disposition_id=? ORDER BY ordinal', (row['id'],))]}


def set_disposition(actor, response_id: str, status: str, reason: str, proposal_ids: list[str], *, program_id: str | None = None, expected_revision: int | None = None, expected_response_revision_id: str | None = None) -> Record:
    with transaction() as connection:
        _ids([response_id], 'response')
        response = get_response(actor, response_id)
        if program_id is not None and response['program_id'] != program_id:
            raise DomainError('forbidden', 'Choose a response in this program.', 403)
        if expected_response_revision_id is not None and expected_response_revision_id != response['current_revision_id']:
            raise DomainError('stale_revision', 'This response changed. Review its current text before recording your preserved disposition.', 409)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision != get_program(actor, response['program_id'])['revision']):
            raise DomainError('stale_revision', 'The program or its classifications changed. Review the current coverage before recording your preserved disposition.', 409)
        if not isinstance(status, str) or status not in STATES:
            raise DomainError('invalid_disposition', 'Choose unreviewed, incorporated, adapted, deferred, or declined.', 422)
        _text(reason, 'an overall disposition reason accounting for every part of the response', 4000, True)
        proposals = _proposals(response['program_id'], proposal_ids)
        disposition_id = str(uuid4())
        connection.execute('INSERT INTO dispositions(id,response_id,response_revision_id,status,reason,entered_by,entered_at) VALUES (?,?,?,?,?,?,?)', (disposition_id, response_id, response['current_revision_id'], status, reason, actor.user_id, utcnow()))
        connection.executemany('INSERT INTO disposition_proposals(disposition_id,proposal_id,ordinal) VALUES (?,?,?)', [(disposition_id, row['id'], index) for index, row in enumerate(proposals)])
        # The serialized transaction binds and clears review for this exact revision.
        connection.execute('UPDATE responses SET review_required=0 WHERE id=? AND revision=?', (response_id, response['revision']))
        _bump(response['program_id'])
        return _disposition_snapshot(connection.execute('SELECT * FROM dispositions WHERE id=?', (disposition_id,)).fetchone())


def coverage(actor, program_id: str) -> list[Record]:
    rows = []
    for number, response in enumerate(list_responses(actor, program_id), 1):
        question = get_db().execute('SELECT text FROM batch_questions WHERE id=?', (response['question_id'],)).fetchone()
        history = [_disposition_snapshot(row) for row in get_db().execute('SELECT * FROM dispositions WHERE response_id=? ORDER BY entered_at,rowid', (response['id'],))]
        incorporation_history = [dict(row) for row in get_db().execute('SELECT * FROM proposal_incorporations WHERE response_id=? ORDER BY recorded_at,rowid', (response['id'],))]
        current = history[-1] if history else None
        status = current['status'] if current and not response['review_required'] and current['response_revision_id'] == response['current_revision_id'] else 'unreviewed'
        linked = [row['proposal_id'] for row in get_db().execute('SELECT proposal_id FROM proposal_responses WHERE response_id=? ORDER BY rowid', (response['id'],))]
        if status == 'unreviewed' and not response['review_required'] and any(row['proposal_id'] in linked and row['response_revision_id'] == response['current_revision_id'] for row in incorporation_history):
            status = 'incorporated'
        rows.append({**response, 'reference': f'Response {number}', 'question_text': question['text'] if question else '', 'status': status, 'reason': current['reason'] if current else '', 'proposal_ids': list(dict.fromkeys(linked + (current['proposal_ids'] if current else []))), 'disposition': current, 'disposition_history': history, 'incorporation_history': incorporation_history})
    return rows


def proposal_evidence(actor, program_id: str, proposal_ids: list[str]) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    by_id = {row['id']: row for row in coverage(actor, program_id)}
    groups = []
    for proposal in _proposals(program_id, proposal_ids):
        responses = []
        for link in get_db().execute('SELECT response_id,response_revision_id FROM proposal_responses WHERE proposal_id=? ORDER BY ordinal', (proposal['id'],)):
            revision = get_db().execute('SELECT text FROM response_revisions WHERE id=? AND response_id=?', (link['response_revision_id'], link['response_id'])).fetchone()
            response = by_id[link['response_id']]
            responses.append({'id': response['id'], 'reference': response['reference'], 'question_text': response['question_text'], 'text': revision['text'], 'response_revision_id': link['response_revision_id']})
        groups.append({'id': proposal['id'], 'title': proposal['title'], 'responses': responses})
    return groups


def _decision_snapshot(row):
    links = [dict(link) for link in get_db().execute('SELECT response_id,response_revision_id FROM decision_responses WHERE decision_id=? ORDER BY ordinal', (row['id'],))]
    proposal_snapshots = [dict(link) for link in get_db().execute('SELECT s.* FROM decision_proposals l JOIN decision_proposal_snapshots s ON s.decision_id=l.decision_id AND s.proposal_id=l.proposal_id WHERE l.decision_id=? ORDER BY l.ordinal', (row['id'],))]
    return {**dict(row), 'proposal_ids': [item['proposal_id'] for item in proposal_snapshots], 'proposal_snapshots': proposal_snapshots, 'response_ids': [link['response_id'] for link in links], 'response_links': links}


def list_decisions(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [_decision_snapshot(row) for row in get_db().execute('SELECT * FROM decisions WHERE program_id=? ORDER BY entered_at,rowid', (program_id,))]


def record_decision(actor, program_id: str, payload: Record, *, ai_receipt: str | None = None) -> Record:
    with transaction() as connection:
        require_role(actor, program_id, {'owner'})
        if not isinstance(payload, dict):
            raise DomainError('invalid_decision', 'Enter the labeled decision fields.', 422)
        outcome = _text(payload.get('outcome', ''), 'the decision outcome', required=True)
        rationale = _text(payload.get('rationale', ''), 'the decision rationale', required=True)
        proposals = _proposals(program_id, payload.get('proposal_ids', []))
        responses = _responses(actor, program_id, payload.get('response_ids', []))
        response_links = {}
        for proposal in proposals:
            for link in connection.execute('SELECT response_id,response_revision_id FROM proposal_responses WHERE proposal_id=? ORDER BY ordinal', (proposal['id'],)):
                response_links.setdefault(link['response_id'], link['response_revision_id'])
        for response in responses:
            response_links.setdefault(response['id'], response['current_revision_id'])
        supersedes_id = payload.get('supersedes_id') or None
        if supersedes_id:
            _row('decisions', supersedes_id, program_id)
            if connection.execute('SELECT 1 FROM decisions WHERE supersedes_id=?', (supersedes_id,)).fetchone():
                raise DomainError('stale_decision', 'This decision has already been superseded. Review its successor first.', 409)
        from onpf.drafting import provenance
        verified = provenance._prepare_save(actor, program_id, 'decision', ai_receipt, record_key=supersedes_id)
        decision_id = str(uuid4())
        connection.execute('INSERT INTO decisions(id,program_id,outcome,rationale,supersedes_id,entered_by,entered_at) VALUES (?,?,?,?,?,?,?)', (decision_id, program_id, outcome, rationale, supersedes_id, actor.user_id, utcnow()))
        connection.executemany('INSERT INTO decision_proposals(decision_id,proposal_id,ordinal) VALUES (?,?,?)', [(decision_id, row['id'], index) for index, row in enumerate(proposals)])
        connection.executemany('INSERT INTO decision_proposal_snapshots(decision_id,proposal_id,proposal_revision,title,text,theme,capture_basis,captured_at) VALUES (?,?,?,?,?,?,?,?)', [(decision_id, row['id'], row['revision'], row['title'], row['text'], row['theme'], 'at_decision', utcnow()) for row in proposals])
        connection.executemany('INSERT INTO decision_responses(decision_id,response_id,response_revision_id,ordinal) VALUES (?,?,?,?)', [(decision_id, response_id, revision_id, index) for index, (response_id, revision_id) in enumerate(response_links.items())])
        _bump(program_id)
        saved = _decision_snapshot(connection.execute('SELECT * FROM decisions WHERE id=?', (decision_id,)).fetchone())
        if verified:
            provenance._attach_verified(actor, program_id, {'kind': 'decision', 'record_key': decision_id}, verified, saved)
        return saved


def validate_document_decisions(actor, program_id, decision_ids):
    require_role(actor, program_id, EDIT_ROLES)
    return [_row('decisions', record_id, program_id)['id'] for record_id in _ids(decision_ids, 'decision')]


def create_review_draft(actor, program_id: str, document_keys: list[str]) -> Record:
    with transaction() as connection:
        require_role(actor,program_id,EDIT_ROLES)
        from onpf.programs.retirement import ensure_editable
        ensure_editable(program_id)
        program = get_program(actor, program_id)
        keys = _ids(document_keys, 'document')
        framework = load_framework()
        if not keys or any(key not in framework['documents'] for key in keys):
            raise DomainError('invalid_documents', 'Select one or more available documents for review.', 422)
        # Literal document text is deliberately shared; no relations or internal records.
        snapshot = {'title': program['title'], 'documents': {key: program['documents'][key] for key in keys}, 'document_titles': {key: framework['documents'][key]['title'] for key in keys}, 'document_sections': {key: [{'key': section['key'], 'label': section['label']} for section in framework['documents'][key]['sections']] for key in keys}}
        from onpf.archives.service import contains_quarantined_copy
        if contains_quarantined_copy(snapshot):
            raise DomainError('quarantined_content', 'Review and remove known copied personal text from the program title and selected documents before creating a new review link. You may select other clean documents.', 409)
        review_id, token, now = str(uuid4()), secrets.token_urlsafe(32), utcnow()
        connection.execute('INSERT INTO review_drafts(id,program_id,program_revision,snapshot,token_hash,created_by,created_at) VALUES (?,?,?,?,?,?,?)', (review_id, program_id, program['revision'], json.dumps(snapshot, ensure_ascii=False), token_hash(token), actor.user_id, now))
        return {'id': review_id, 'token': token, 'program_revision': program['revision'], 'created_at': now}


def list_review_drafts(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [dict(row) for row in get_db().execute('SELECT id,program_revision,created_at,revoked_at FROM review_drafts WHERE program_id=? ORDER BY created_at,rowid', (program_id,))]


def read_review_draft(token: str) -> Record:
    if not isinstance(token, str) or len(token) > 200:
        raise DomainError('not_found', 'This review link is unavailable.', 404)
    row = get_db().execute('SELECT r.snapshot,r.created_at FROM review_drafts r JOIN programs p ON p.id=r.program_id WHERE r.token_hash=? AND r.revoked_at IS NULL AND p.retired_at IS NULL', (token_hash(token),)).fetchone()
    if row is None:
        raise DomainError('not_found', 'This review link is unavailable or revoked.', 404)
    return {**json.loads(row['snapshot']), 'created_at': row['created_at']}


def revoke_review_draft(actor, review_id: str) -> None:
    with transaction() as connection:
        row = connection.execute('SELECT program_id FROM review_drafts WHERE id=?', (review_id,)).fetchone()
        if row is None:
            raise DomainError('not_found', 'This review draft is unavailable.', 404)
        require_role(actor, row['program_id'], EDIT_ROLES)
        connection.execute('UPDATE review_drafts SET revoked_at=COALESCE(revoked_at,?) WHERE id=?', (utcnow(), review_id))
