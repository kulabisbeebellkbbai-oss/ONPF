"""Domain services. Contributor answers never establish owner decisions."""
import json
import re
import secrets
from datetime import date
from uuid import uuid4

from onpf.auth.models import Principal
from onpf.auth.service import require_role, token_hash
from onpf.db import Record, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.programs.framework import load_framework
from onpf.programs.service import get_program

EDIT_ROLES = {'owner', 'facilitator'}
DEFAULT_INTAKE = ('You may share multiple responses to every question, using a name, alias, or anonymously. '
                  'Your input is preserved for refinement. Inclusion does not mean adoption into the final program. '
                  'Designated decision owners make final design decisions; consensus is optional. '
                  'Contributor details are excluded from public exports by default.')


def _text(value, label, maximum=8000, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise DomainError('invalid_text', f'Enter {label} as text of at most {maximum} characters.', 422)
    return value


def _record(payload):
    if not isinstance(payload, dict):
        raise DomainError('invalid_payload', 'Use the labeled inquiry fields.', 422)


def _revision(program_id, expected_revision):
    row = get_db().execute('SELECT revision FROM programs WHERE id=?', (program_id,)).fetchone()
    if type(expected_revision) is not int or row['revision'] != expected_revision:
        raise DomainError('stale_revision', 'The program changed. Your entries are preserved; reconcile with the current version before saving.', 409)


def _touch(program_id):
    get_db().execute('UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?', (utcnow(), program_id))


def decision_fields(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [{**dict(row), 'depends_on': json.loads(row['depends_on'])} for row in get_db().execute('SELECT * FROM decision_fields WHERE program_id=? ORDER BY key', (program_id,))]


def _validate_fields(fields):
    for field in fields.values():
        dependencies = field['depends_on']
        if not isinstance(dependencies, list) or any(not isinstance(key, str) or key not in fields for key in dependencies):
            raise DomainError('missing_dependency', 'Choose existing owner decision fields as dependencies.', 422)
    visiting, visited = set(), set()
    def visit(key):
        if key in visiting:
            raise DomainError('dependency_cycle', 'Owner decision dependencies cannot form a cycle.', 422)
        if key not in visited:
            visiting.add(key)
            for dependency in fields[key]['depends_on']:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
    for key in fields:
        visit(key)


def set_decision_field(actor, program_id: str, payload: Record, expected_revision: int) -> Record:
    """An explicit owner action; value=None means unresolved. Return updated program."""
    _record(payload)
    with transaction() as connection:
        require_role(actor, program_id, {'owner'})
        _revision(program_id, expected_revision)
        fields = {field['key']: field for field in decision_fields(actor, program_id)}
        key = payload.get('key', '')
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}', key):
            raise DomainError('invalid_field', 'Use a decision key beginning with a letter, with letters, numbers, hyphens or underscores.', 422)
        field = {**fields.get(key, {'label': key, 'value': None, 'depends_on': []}), **{k: v for k, v in payload.items() if k in {'label', 'value', 'depends_on'}}}
        _text(field['label'], 'a decision label', 200, True)
        if field['value'] is not None:
            _text(field['value'], 'a decision value', 2000, True)
        fields[key] = field
        _validate_fields(fields)
        connection.execute('INSERT INTO decision_fields(program_id,key,label,value,depends_on,decided_by,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(program_id,key) DO UPDATE SET label=excluded.label,value=excluded.value,depends_on=excluded.depends_on,decided_by=excluded.decided_by,updated_at=excluded.updated_at', (program_id, key, field['label'], field['value'], json.dumps(field['depends_on']), actor.user_id if field['value'] is not None else None, utcnow()))
        _touch(program_id)
        return get_program(actor, program_id)


def _questions(actor, program_id):
    program = get_program(actor, program_id)
    framework = load_framework()
    prompts = {}
    for key in ['core'] + program['module_keys']:
        for prompt in framework['modules'][key]['prompts']:
            if prompt['id'] in prompts:
                raise DomainError('duplicate_question', 'Framework question IDs must be unique.', 422)
            prompts[prompt['id']] = dict(prompt)
    for row in get_db().execute('SELECT id,content FROM inquiry_questions WHERE program_id=? ORDER BY updated_at,id', (program_id,)):
        prompts[row['id']] = json.loads(row['content'])
    return list(prompts.values())


def _validate_question(question, fields):
    _text(question.get('text'), 'a question', required=True)
    if type(question.get('stage')) is not int or question['stage'] not in range(1, 8):
        raise DomainError('invalid_stage', 'Choose a development stage from 1 to 7.', 422)
    if question.get('answer_type') != 'text' or question.get('document_key') not in load_framework()['documents']:
        raise DomainError('invalid_question', 'Choose an available document; questions accept text answers.', 422)
    dependencies = question.get('depends_on', [])
    if not isinstance(dependencies, list):
        raise DomainError('missing_dependency', 'Question dependencies must name owner decision fields.', 422)
    for dependency in dependencies:
        if not isinstance(dependency, dict) or set(dependency) != {'field', 'equals'} or not isinstance(dependency['field'], str) or dependency['field'] not in fields:
            raise DomainError('missing_dependency', 'Each question dependency must reference an existing owner decision field and required value.', 422)
        _text(dependency['equals'], 'a required decision value', 2000, True)


def save_question(actor, program_id: str, payload: Record, expected_revision: int, *, ai_receipt: str | None = None) -> Record:
    """Edit a local template override, or add a custom question without an id."""
    _record(payload)
    with transaction() as connection:
        require_role(actor, program_id, EDIT_ROLES)
        _revision(program_id, expected_revision)
        existing = {q['id']: q for q in _questions(actor, program_id)}
        question_id = payload.get('id')
        if question_id is not None and (not isinstance(question_id, str) or question_id not in existing):
            raise DomainError('not_found', 'This draft question is unavailable.', 404)
        question = {**existing.get(question_id, {'id': question_id, 'stage': 1, 'answer_type': 'text', 'document_key': 'overview', 'depends_on': []}), **{k: v for k, v in payload.items() if k in {'text', 'stage', 'answer_type', 'document_key', 'depends_on', 'why', 'group_label', 'source_type', 'source_id'}}}
        _validate_question(question, {f['key']: f for f in decision_fields(actor, program_id)})
        _text(question.get('why', ''), 'why this question is needed', 4000)
        _text(question.get('group_label', ''), 'a question group', 200)
        from onpf.drafting import provenance
        verified = provenance._prepare_save(actor, program_id, 'questions', ai_receipt, record_key=question_id,
                                            stage=question['stage'], document_key=question['document_key'])
        question_id = question_id or str(uuid4())
        question['id'] = question_id
        connection.execute('INSERT INTO inquiry_questions(program_id,id,content,updated_at) VALUES (?,?,?,?) ON CONFLICT(program_id,id) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at', (program_id, question_id, json.dumps(question, ensure_ascii=False), utcnow()))
        _touch(program_id)
        context = connection.execute('SELECT reason,sources,context_revision FROM question_contexts WHERE program_id=? AND question_id=?', (program_id, question_id)).fetchone()
        if context:
            sources = json.loads(context['sources'])
            question.update(reason=context['reason'], sources=sources, source_handles=[source['handle'] for source in sources])
        if verified:
            provenance._attach_verified(actor, program_id, {'kind': 'questions', 'record_key': question_id}, verified, question)
        return question


def question_catalog(actor, program_id: str) -> list[Record]:
    """All selected-module/local questions, with explicit reasons for deferral."""
    require_role(actor, program_id, EDIT_ROLES)
    fields = {f['key']: f for f in decision_fields(actor, program_id)}
    _validate_fields(fields)
    def decided(key):
        return fields[key]['value'] is not None and all(decided(dep) for dep in fields[key]['depends_on'])
    results = []
    for question in _questions(actor, program_id):
        _validate_question(question, fields)
        missing = [f"{fields[dep['field']]['label']} must be decided as {dep['equals']}" for dep in question['depends_on'] if not decided(dep['field']) or fields[dep['field']]['value'] != dep['equals']]
        context = get_db().execute('SELECT reason,sources,context_revision FROM question_contexts WHERE program_id=? AND question_id=?', (program_id, question['id'])).fetchone()
        sources = json.loads(context['sources']) if context else []
        results.append({**question, 'relevant': not missing, 'deferred_reason': '; '.join(missing),
                        'reason': context['reason'] if context else '', 'sources': sources,
                        'context_revision': context['context_revision'] if context else 0,
                        'source_handles': [source['handle'] for source in sources]})
    return results


def available_questions(actor, program_id: str) -> list[Record]:
    return [question for question in question_catalog(actor, program_id) if question['relevant']]


def _clarification_source(program_id: str, value: Record) -> Record:
    if not isinstance(value, dict):
        raise DomainError('invalid_source', 'Identify the source of each clarification question.', 422)
    kind = value.get('source_type', 'program')
    identifier = value.get('source_id', program_id) or program_id
    if kind == 'program':
        valid = identifier == program_id
    elif kind == 'response':
        valid = get_db().execute('SELECT 1 FROM responses r JOIN submissions s ON s.id=r.submission_id WHERE r.id=? AND s.program_id=?', (identifier, program_id)).fetchone()
    elif kind in ('proposal', 'decision'):
        table = 'proposals' if kind == 'proposal' else 'decisions'
        valid = get_db().execute(f'SELECT 1 FROM {table} WHERE id=? AND program_id=?', (identifier, program_id)).fetchone()
    elif kind == 'document':
        document, separator, section = identifier.partition(':') if isinstance(identifier, str) else ('', '', '')
        valid = bool(separator and any(item['key'] == section for item in load_framework()['documents'].get(document, {}).get('sections', [])))
    else:
        valid = False
    if not valid:
        raise DomainError('invalid_source', 'Choose a source in this program for each clarification question.', 422)
    return {'source_type': kind, 'source_id': identifier,
            'why': _text(value.get('why', ''), 'why the question is needed', 2000, True).strip(),
            'group_label': _text(value.get('group_label', ''), 'a question group', 200).strip()}


def issue_batch(actor, program_id: str, payload: Record) -> Record:
    _record(payload)
    with transaction() as connection:
        require_role(actor, program_id, EDIT_ROLES)
        catalog = {q['id']: q for q in question_catalog(actor, program_id)}
        selected = payload.get('question_ids', [q['id'] for q in catalog.values() if q['relevant']])
        if not isinstance(selected, list) or not selected or len(selected) > 500 or any(not isinstance(key, str) or key not in catalog for key in selected) or len(set(selected)) != len(selected):
            raise DomainError('invalid_questions', 'Choose one or more available questions, without duplicates.', 422)
        overrides = payload.get('overrides', {})
        if not isinstance(overrides, dict) or set(overrides) - set(selected):
            raise DomainError('invalid_override', 'Overrides must belong to selected questions.', 422)
        if overrides:
            require_role(actor, program_id, {'owner'})
            for reason in overrides.values():
                _text(reason, 'a reason for the owner override', 2000, True)
        for key in selected:
            if not catalog[key]['relevant'] and key not in overrides:
                raise DomainError('deferred_question', 'A deferred question needs an owner override with a reason.', 422)
        title = _text(payload.get('title', 'Community-program inquiry'), 'a batch title', 200, True)
        instructions = _text(payload.get('instructions', ''), 'instructions')
        target_group = _text(payload.get('target_group', ''), 'a target group', 500)
        due_date = payload.get('due_date') or None
        if due_date is not None:
            try:
                if not isinstance(due_date, str) or date.fromisoformat(due_date).isoformat() != due_date:
                    raise ValueError
            except ValueError:
                raise DomainError('invalid_date', 'Choose a due date, or leave it blank. Due dates are advisory.', 422)
        intake = _text(payload.get('intake_copy', DEFAULT_INTAKE), 'intake information', required=True)
        if intake != DEFAULT_INTAKE:
            require_role(actor, program_id, {'owner'})
            if payload.get('intake_copy_approved') is not True:
                raise DomainError('unapproved_intake', 'The owner must approve customized intake information before issuing it.', 422)
        clarification = payload.get('clarification')
        if clarification is not None:
            if not isinstance(clarification, dict):
                raise DomainError('invalid_round', 'Describe the clarification round.', 422)
            after_step = clarification.get('after_step', '')
            if after_step not in ('after_inquiry', 'after_proposals', 'after_decisions', 'document_drafting', 'document_review', 'approval_preparation'):
                raise DomainError('invalid_round', 'Choose the workflow step that raised this clarification.', 422)
            purpose = _text(clarification.get('purpose', ''), 'the round purpose', 2000, True).strip()
            sources = clarification.get('sources', {})
            if not isinstance(sources, dict) or set(sources) != set(selected):
                raise DomainError('invalid_source', 'Describe why and where each selected question arose.', 422)
            sources = {key: _clarification_source(program_id, sources[key]) for key in selected}
        batch_id, now = str(uuid4()), utcnow()
        version = connection.execute('SELECT COALESCE(MAX(version),0)+1 FROM batches WHERE program_id=?', (program_id,)).fetchone()[0]
        connection.execute('INSERT INTO batches(id,program_id,version,title,instructions,target_group,due_date,intake_copy,framework_version,issued_by,issued_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (batch_id, program_id, version, title, instructions, target_group, due_date, intake, load_framework()['version'], actor.user_id, now))
        if clarification is not None:
            round_number = connection.execute('SELECT COALESCE(MAX(round_number),0)+1 FROM clarification_rounds WHERE program_id=?', (program_id,)).fetchone()[0]
            connection.execute('INSERT INTO clarification_rounds(batch_id,program_id,round_number,after_step,purpose) VALUES (?,?,?,?,?)', (batch_id, program_id, round_number, after_step, purpose))
        for ordinal, key in enumerate(selected):
            question = catalog[key]
            issued_id = str(uuid4())
            connection.execute('INSERT INTO batch_questions(id,batch_id,source_id,stage,text,answer_type,document_key,depends_on,override_reason,ordinal) VALUES (?,?,?,?,?,?,?,?,?,?)', (issued_id, batch_id, key, question['stage'], question['text'], question['answer_type'], question['document_key'], json.dumps(question['depends_on']), overrides.get(key, ''), ordinal))
            if clarification is not None:
                source = sources[key]
                connection.execute('INSERT INTO clarification_question_sources(question_id,source_type,source_id,why,group_label) VALUES (?,?,?,?,?)', (issued_id, source['source_type'], source['source_id'], source['why'], source['group_label']))
        if clarification is not None:
            _touch(program_id)
        return get_batch(actor, batch_id)


def _batch_row(batch_id):
    row = get_db().execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
    if not row:
        raise DomainError('not_found', 'This inquiry batch is unavailable.', 404)
    return dict(row)


def authorize_batch(actor, batch_id: str, *, submitting=False) -> Record:
    """Task 4 must call inside its write transaction, even for cached invite principals."""
    batch = _batch_row(batch_id)
    if not isinstance(actor, Principal):
        raise DomainError('forbidden', 'This batch is unavailable to you.', 403)
    if actor.invite_id or actor.batch_id:
        row = get_db().execute('SELECT batch_id,revoked_at FROM invitations WHERE id=?', (actor.invite_id,)).fetchone()
        if actor.user_id or actor.batch_id != batch_id or not row or row['batch_id'] != batch_id or row['revoked_at'] or batch['closed_at']:
            raise DomainError('forbidden', 'This invitation has been revoked or its batch is closed.', 403)
    else:
        require_role(actor, batch['program_id'], EDIT_ROLES)
        if submitting and batch['closed_at']:
            raise DomainError('batch_closed', 'This batch is closed to new submissions.', 403)
    return batch


def get_batch(actor, batch_id: str) -> Record:
    batch = authorize_batch(actor, batch_id)
    clarification = get_db().execute('SELECT round_number,after_step,purpose FROM clarification_rounds WHERE batch_id=?', (batch_id,)).fetchone()
    program_context = dict(get_db().execute(
        'SELECT title,purpose,local_context,operating_status FROM programs WHERE id=?',
        (batch['program_id'],),
    ).fetchone())
    questions = [{**dict(row), 'depends_on': json.loads(row['depends_on'])} for row in get_db().execute('SELECT * FROM batch_questions WHERE batch_id=? ORDER BY ordinal', (batch_id,))]
    if actor.invite_id:
        # Intake projection excludes workspace content, owner identities and program IDs.
        batch = {key: batch[key] for key in ('id', 'version', 'title', 'instructions', 'target_group', 'due_date', 'intake_copy', 'closed_at')}
        questions = [{key: q[key] for key in ('id', 'source_id', 'stage', 'text', 'answer_type', 'ordinal')} for q in questions]
    else:
        round_context = get_db().execute('SELECT stage,kind,reason,sources FROM drafting_clarification_rounds WHERE batch_id=?', (batch_id,)).fetchone()
        if round_context:
            batch.update(stage=round_context['stage'], kind=round_context['kind'], reason=round_context['reason'], sources=json.loads(round_context['sources']))
        for question in questions:
            context = get_db().execute('SELECT reason,sources FROM clarification_round_questions WHERE batch_id=? AND question_id=?', (batch_id, question['id'])).fetchone()
            if context:
                question.update(reason=context['reason'], sources=json.loads(context['sources']))
    if clarification and not actor.invite_id:
        for question in questions:
            source = get_db().execute('SELECT source_type,source_id,why,group_label FROM clarification_question_sources WHERE question_id=?', (question['id'],)).fetchone()
            deferred = get_db().execute('SELECT reason,deferred_at FROM clarification_deferrals WHERE question_id=?', (question['id'],)).fetchone()
            count = get_db().execute('SELECT COUNT(*) FROM responses WHERE question_id=?', (question['id'],)).fetchone()[0]
            question.update({'clarification_source': dict(source) if source else None,
                             'deferral': dict(deferred) if deferred else None, 'response_count': count})
    return {**batch, 'questions': questions, 'program_context': program_context,
            'clarification': dict(clarification) if clarification and not actor.invite_id else None}


def get_question(actor, batch_id: str, question_id: str) -> Record:
    batch = get_batch(actor, batch_id)
    for question in batch['questions']:
        if question['id'] == question_id:
            return question
    raise DomainError('not_found', 'This question is unavailable in this batch.', 404)


def list_batches(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    return [dict(row) for row in get_db().execute('SELECT b.id,b.version,b.title,b.issued_at,b.closed_at,c.round_number,c.after_step FROM batches b LEFT JOIN clarification_rounds c ON c.batch_id=b.id WHERE b.program_id=? ORDER BY b.version DESC', (program_id,))]


def clarification_overview(actor, program_id: str) -> list[Record]:
    require_role(actor, program_id, EDIT_ROLES)
    result = []
    for row in get_db().execute('SELECT b.id,b.title,c.round_number,c.after_step,c.purpose FROM clarification_rounds c JOIN batches b ON b.id=c.batch_id WHERE c.program_id=? ORDER BY c.round_number', (program_id,)):
        total = get_db().execute('SELECT COUNT(*) FROM batch_questions WHERE batch_id=?', (row['id'],)).fetchone()[0]
        answered = get_db().execute('SELECT COUNT(DISTINCT q.id) FROM batch_questions q JOIN responses r ON r.question_id=q.id WHERE q.batch_id=?', (row['id'],)).fetchone()[0]
        deferred = get_db().execute('SELECT COUNT(*) FROM batch_questions q JOIN clarification_deferrals d ON d.question_id=q.id WHERE q.batch_id=? AND NOT EXISTS(SELECT 1 FROM responses r WHERE r.question_id=q.id)', (row['id'],)).fetchone()[0]
        result.append({**dict(row), 'total': total, 'answered': answered, 'deferred': deferred, 'pending': total - answered - deferred})
    from onpf.inquiries.clarifications import rounds
    for batch in rounds(actor, program_id):
        # Both workflows share candidate readiness, while each issued context
        # remains in its own immutable history. Count explicit deferrals only
        # while unanswered, matching the production candidate acknowledgement.
        answered = sum(question['answered'] for question in batch['questions'])
        deferred = sum(not question['answered'] and bool(question['deferrals'])
                       for question in batch['questions'])
        total = len(batch['questions'])
        result.append({'id': batch['id'], 'title': batch['title'],
                       'round_number': batch['version'], 'after_step': '',
                       'purpose': batch['reason'], 'kind': batch['kind'], 'stage': batch['stage'],
                       'total': total, 'answered': answered, 'deferred': deferred,
                       'pending': total - answered - deferred})
    return result


def defer_clarification_question(actor, question_id: str, reason: str) -> None:
    with transaction() as connection:
        row = connection.execute('SELECT c.program_id FROM clarification_question_sources s JOIN batch_questions q ON q.id=s.question_id JOIN clarification_rounds c ON c.batch_id=q.batch_id WHERE s.question_id=?', (question_id,)).fetchone()
        if not row:
            raise DomainError('not_found', 'This clarification question is unavailable.', 404)
        require_role(actor, row['program_id'], EDIT_ROLES)
        explanation = _text(reason, 'a deferral reason', 2000, True).strip()
        connection.execute('INSERT INTO clarification_deferrals(question_id,reason,deferred_by,deferred_at) VALUES (?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET reason=excluded.reason,deferred_by=excluded.deferred_by,deferred_at=excluded.deferred_at', (question_id, explanation, actor.user_id, utcnow()))
        _touch(row['program_id'])


def create_invitation(actor, batch_id: str) -> str:
    with transaction() as connection:
        batch = _batch_row(batch_id)
        require_role(actor, batch['program_id'], EDIT_ROLES)
        from onpf.programs.retirement import ensure_editable
        ensure_editable(batch['program_id'])
        if batch['closed_at']:
            raise DomainError('batch_closed', 'This batch is closed.', 403)
        token = secrets.token_urlsafe(32)
        connection.execute('INSERT INTO invitations(id,batch_id,token_hash,created_at) VALUES (?,?,?,?)', (str(uuid4()), batch_id, token_hash(token), utcnow()))
        return token


def resolve_invitation(token: str) -> Principal:
    if not isinstance(token, str) or not token or len(token) > 256:
        raise DomainError('forbidden', 'This invitation is unavailable.', 403)
    row = get_db().execute('SELECT i.id,i.batch_id,i.revoked_at,b.closed_at,p.retired_at FROM invitations i JOIN batches b ON b.id=i.batch_id JOIN programs p ON p.id=b.program_id WHERE i.token_hash=?', (token_hash(token),)).fetchone()
    if not row or row['revoked_at'] or row['closed_at'] or row['retired_at']:
        raise DomainError('forbidden', 'This invitation has been revoked or its batch is closed.', 403)
    return Principal(None, row['id'], row['batch_id'])


def list_invitations(actor, batch_id: str) -> list[Record]:
    batch = _batch_row(batch_id)
    require_role(actor, batch['program_id'], EDIT_ROLES)
    return [dict(row) for row in get_db().execute('SELECT id,created_at,revoked_at FROM invitations WHERE batch_id=? ORDER BY created_at', (batch_id,))]


def revoke_invitation(actor, invite_id: str) -> None:
    with transaction() as connection:
        row = connection.execute('SELECT i.batch_id,b.program_id FROM invitations i JOIN batches b ON b.id=i.batch_id WHERE i.id=?', (invite_id,)).fetchone()
        if not row:
            raise DomainError('not_found', 'This invitation is unavailable.', 404)
        require_role(actor, row['program_id'], EDIT_ROLES)
        connection.execute('UPDATE invitations SET revoked_at=COALESCE(revoked_at,?) WHERE id=?', (utcnow(), invite_id))


def close_batch(actor, batch_id: str) -> None:
    with transaction() as connection:
        batch = _batch_row(batch_id)
        require_role(actor, batch['program_id'], EDIT_ROLES)
        connection.execute('UPDATE batches SET closed_at=COALESCE(closed_at,?) WHERE id=?', (utcnow(), batch_id))
