"""Transactional capture; raw inputs are restricted to program editors."""
import hashlib
import json
from uuid import uuid4

from onpf.auth.models import Principal
from onpf.auth.service import require_role
from onpf.db import Record, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.inquiries.service import authorize_batch

EDIT_ROLES = {'owner', 'facilitator'}
INPUT_FIELDS = {'question_id', 'text', 'answer_state', 'attribution', 'display_name', 'entry_method', 'publication_permission'}


def _text(value, label, maximum=50000, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()) or '\x00' in value:
        raise DomainError('invalid_text', f'Enter {label} as text of at most {maximum} characters.', 422)
    return value


def _validate_response(actor, value, question_ids):
    if not isinstance(value, dict) or set(value) - INPUT_FIELDS:
        raise DomainError('invalid_response', 'Use the labeled response fields. The person entering a response is recorded automatically.', 422)
    if not isinstance(value.get('question_id'), str) or value['question_id'] not in question_ids:
        raise DomainError('invalid_question', 'Every response must belong to a question in this batch.', 422)
    row = {'question_id': value['question_id'], 'text': value.get('text', ''),
           'answer_state': value.get('answer_state', 'answered'),
           'attribution': value.get('attribution', 'anonymous'),
           'display_name': value.get('display_name'), 'entry_method': value.get('entry_method', 'direct'),
           'publication_permission': value.get('publication_permission', 'none')}
    choices = {'answer_state': {'answered', 'abstain', 'not_applicable'},
               'attribution': {'named', 'alias', 'anonymous', 'group'},
               'entry_method': {'direct', 'paper', 'meeting'},
               'publication_permission': {'none', 'anonymous_quote', 'attributed_quote'}}
    for key, allowed in choices.items():
        if not isinstance(row[key], str) or row[key] not in allowed:
            raise DomainError('invalid_response', f'Choose a valid {key.replace("_", " ")}.', 422)
    _text(row['text'], 'a response', required=row['answer_state'] == 'answered')
    if row['display_name'] is not None:
        _text(row['display_name'], 'a name, alias, or group label', 200)
    if row['attribution'] == 'anonymous':
        row['display_name'] = None
    else:
        _text(row['display_name'], 'a name, alias, or group label', 200, True)
    if actor.invite_id and row['entry_method'] != 'direct':
        raise DomainError('invalid_entry_method', 'Invitation responses are direct contributions. Facilitators enter paper or meeting responses through their account.', 422)
    if row['publication_permission'] == 'attributed_quote' and row['attribution'] == 'anonymous':
        raise DomainError('invalid_permission', 'An attributed quotation needs a name, alias, or group label; choose anonymous quotation or no quotation for anonymous input.', 422)
    return row


def _receipt(submission_id):
    return {'submission_id': submission_id, 'response_ids': [row['id'] for row in get_db().execute('SELECT id FROM responses WHERE submission_id=? ORDER BY ordinal', (submission_id,))]}


def submit_responses(actor: Principal, batch_id: str, submission_key: str, responses: list[Record]) -> Record:
    """A retry key belongs to one program and persisted account/invitation.

    Only normalized capture fields are hashed; no duplicate private payload is
    retained in the idempotency record. Identical input with a new key is new input.
    """
    with transaction() as connection:
        batch = authorize_batch(actor, batch_id, submitting=True)
        _text(submission_key, 'a submission reference', 200, True)
        if not isinstance(responses, list) or not responses or len(responses) > 500:
            raise DomainError('invalid_responses', 'Enter one or more responses, up to 500 per submission. Skipped questions create no answer.', 422)
        question_ids = {row['id'] for row in connection.execute('SELECT id FROM batch_questions WHERE batch_id=?', (batch_id,))}
        rows = [_validate_response(actor, row, question_ids) for row in responses]
        payload = json.dumps({'batch_id': batch_id, 'responses': rows}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        actor_key = 'invite:' + actor.invite_id if actor.invite_id else 'user:' + actor.user_id
        existing = connection.execute('SELECT id,payload_hash FROM submissions WHERE program_id=? AND actor_key=? AND submission_key=?', (batch['program_id'], actor_key, submission_key)).fetchone()
        if existing:
            if existing['payload_hash'] != digest:
                raise DomainError('submission_conflict', 'This submission reference was already used with different answers. Keep these answers and start a new submission.', 409)
            return _receipt(existing['id'])
        submission_id, now = str(uuid4()), utcnow()
        connection.execute('INSERT INTO submissions(id,program_id,batch_id,actor_key,submission_key,payload_hash,submitted_by,invite_id,submitted_at) VALUES (?,?,?,?,?,?,?,?,?)', (submission_id, batch['program_id'], batch_id, actor_key, submission_key, digest, actor.user_id, actor.invite_id, now))
        for ordinal, row in enumerate(rows):
            response_id = str(uuid4())
            connection.execute('INSERT INTO responses(id,submission_id,batch_id,question_id,ordinal,text,answer_state,attribution,display_name,entry_method,publication_permission,entered_by,entered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (response_id, submission_id, batch_id, row['question_id'], ordinal, row['text'], row['answer_state'], row['attribution'], row['display_name'], row['entry_method'], row['publication_permission'], actor.user_id, now))
            connection.execute('INSERT INTO response_revisions(id,response_id,revision,text,reason,entered_by,entered_at) VALUES (?,?,?,?,?,?,?)', (str(uuid4()), response_id, 1, row['text'], '', actor.user_id, now))
        return _receipt(submission_id)


def _response_row(response_id):
    row = get_db().execute('SELECT r.*,s.program_id FROM responses r JOIN submissions s ON s.id=r.submission_id WHERE r.id=?', (response_id,)).fetchone()
    if row is None:
        raise DomainError('not_found', 'This response is unavailable.', 404)
    return dict(row)


def get_response(actor, response_id: str) -> Record:
    row = _response_row(response_id)
    require_role(actor, row['program_id'], EDIT_ROLES)
    history = [dict(item) for item in get_db().execute('SELECT * FROM response_revisions WHERE response_id=? ORDER BY revision', (response_id,))]
    duplicates = [dict(item) for item in get_db().execute('SELECT * FROM response_duplicates WHERE response_id=? ORDER BY entered_at,rowid', (response_id,))]
    event = get_db().execute('SELECT reason,recorded_at FROM redaction_events WHERE response_id=?', (response_id,)).fetchone()
    return {**row, 'original_text': row['text'], 'text': history[-1]['text'],
            'current_revision_id': history[-1]['id'], 'history': history,
            'duplicate_of': duplicates[-1]['original_id'] if duplicates else None,
            'duplicate_history': duplicates, 'redaction': dict(event) if event else None}


def list_responses(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    ids = [row['id'] for row in get_db().execute('SELECT r.id FROM responses r JOIN submissions s ON s.id=r.submission_id WHERE s.program_id=? ORDER BY s.submitted_at,s.rowid,r.ordinal', (program_id,))]
    return [get_response(actor, response_id) for response_id in ids]


def get_receipt(actor, submission_id: str) -> Record:
    """Editor receipt projection only. Invitees use their signed browser receipt.

    The shared invitation itself grants no submission or response read permission.
    """
    row = get_db().execute('SELECT program_id FROM submissions WHERE id=?', (submission_id,)).fetchone()
    if not row:
        raise DomainError('not_found', 'This receipt is unavailable.', 404)
    require_role(actor, row['program_id'], EDIT_ROLES)
    return _receipt(submission_id)


def revise_response(actor, response_id: str, text: str, reason: str, expected_revision: int) -> Record:
    """Append requested corrections, preserving original input and old revision IDs.

    Task 5 must treat review_required as unreviewed and bind dispositions to the
    current revision. Task 7 must compare frozen revision IDs on final approval.
    Neither operation may mutate previously released revision scopes.
    """
    with transaction() as connection:
        row = _response_row(response_id)
        require_role(actor, row['program_id'], EDIT_ROLES)
        if connection.execute('SELECT 1 FROM redaction_events WHERE response_id=?', (response_id,)).fetchone():
            raise DomainError('removed_response', 'Removed input cannot be recaptured through correction. Record any newly authorized input separately.', 409)
        _text(text, 'the corrected response', required=row['answer_state'] == 'answered')
        _text(reason, 'the reason for this requested correction', 4000, True)
        if type(expected_revision) is not int or row['revision'] != expected_revision:
            raise DomainError('stale_revision', 'This response changed. Review the current history before recording your correction.', 409)
        revision = row['revision'] + 1
        connection.execute('INSERT INTO response_revisions(id,response_id,revision,text,reason,entered_by,entered_at) VALUES (?,?,?,?,?,?,?)', (str(uuid4()), response_id, revision, text, reason, actor.user_id, utcnow()))
        connection.execute('UPDATE responses SET revision=?,review_required=1 WHERE id=?', (revision, response_id))
        return get_response(actor, response_id)


def mark_duplicate(actor, response_id: str, original_id: str, reason: str) -> None:
    """Retain entries and append reasoned duplicate markers, never merge or delete."""
    with transaction() as connection:
        row = _response_row(response_id)
        require_role(actor, row['program_id'], EDIT_ROLES)
        original = _response_row(original_id)
        if original['program_id'] != row['program_id']:
            raise DomainError('forbidden', 'Duplicate links must stay within the same program.', 403)
        _text(reason, 'a reason for the duplicate marker', 4000, True)
        visited, cursor = {response_id}, original_id
        while cursor:
            if cursor in visited:
                raise DomainError('duplicate_cycle', 'A duplicate cannot refer to itself or form a circular link.', 422)
            visited.add(cursor)
            previous = connection.execute('SELECT original_id FROM response_duplicates WHERE response_id=? ORDER BY entered_at DESC,rowid DESC LIMIT 1', (cursor,)).fetchone()
            cursor = previous['original_id'] if previous else None
        current = connection.execute('SELECT original_id,reason FROM response_duplicates WHERE response_id=? ORDER BY entered_at DESC,rowid DESC LIMIT 1', (response_id,)).fetchone()
        if current and current['original_id'] == original_id and current['reason'] == reason:
            return
        now = utcnow()
        connection.execute('INSERT INTO response_duplicates(id,response_id,original_id,reason,entered_by,entered_at) VALUES (?,?,?,?,?,?)', (str(uuid4()), response_id, original_id, reason, actor.user_id, now))
        connection.execute('UPDATE responses SET review_required=1 WHERE id=?', (response_id,))
        connection.execute('UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?', (now, row['program_id']))
