"""Private, repeatable batch-backed clarification rounds and explicit deferrals."""
import json
from uuid import uuid4

from onpf.auth.service import require_role
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.inquiries import service


def question_context(actor, program_id: str, question_id: str) -> dict:
    require_role(actor, program_id, service.EDIT_ROLES)
    if question_id not in {q['id'] for q in service._questions(actor, program_id)}:
        raise DomainError('not_found', 'This draft question is unavailable.', 404)
    row = get_db().execute('SELECT * FROM question_contexts WHERE program_id=? AND question_id=?',
                           (program_id, question_id)).fetchone()
    return {**dict(row), 'sources': json.loads(row['sources'])} if row else {
        'program_id': program_id, 'question_id': question_id, 'reason': '', 'sources': []}


def _persist_context(actor, program_id, question_id, reason, manifests):
    get_db().execute('INSERT INTO question_contexts(program_id,question_id,reason,sources,updated_by,updated_at) '
                     'VALUES (?,?,?,?,?,?) ON CONFLICT(program_id,question_id) DO UPDATE SET '
                     'reason=excluded.reason,sources=excluded.sources,context_revision=question_contexts.context_revision+1,'
                     'updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                     (program_id, question_id, reason, json.dumps(manifests, ensure_ascii=False), actor.user_id, utcnow()))


def context_options(actor, program_id: str, question_id: str) -> list[dict]:
    """Current content-free authorized choices for an explicit context Save."""
    from onpf.drafting import evidence
    question_context(actor, program_id, question_id)
    return [evidence._manifest(source) for source in evidence.catalog(actor, program_id,
            {'kind': 'questions'})]


def _current_context_sources(actor, program_id, manifests):
    """Accept only unchanged content behind older aggregate context revisions.

    Manual context and issuance compose inside the domain write transaction.
    Receipt validation deliberately keeps evidence.assert_current's exact rule.
    New snapshots use current safe manifests without rewriting issued history.
    """
    from onpf.drafting import evidence
    if not get_db().in_transaction:
        raise RuntimeError('Context reconciliation requires the domain transaction')
    available = {source['handle']: evidence._manifest(source) for source in evidence._catalog(actor, program_id)}
    if not isinstance(manifests, list) or any(not isinstance(source, dict) for source in manifests):
        raise DomainError('invalid_sources', 'Use content-free context source manifests.', 422)
    evidence._handles([source.get('handle') for source in manifests])
    results = []
    for source in manifests:
        current = available.get(source['handle'])
        compared = source
        if (current is not None and current['kind'] in {'program', 'document', 'decision_field'}
                and type(source.get('revision')) is int and 1 <= source['revision'] <= current['revision']):
            compared = {**source, 'revision': current['revision']}
        try:
            matches = current is not None and evidence._encode(compared) == evidence._encode(current)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise DomainError('stale_evidence', 'Context sources changed or became unavailable. Review current support before saving or issuing.', 409)
        results.append(current)
    return results


def _accepted_contexts(actor, program_id, question_ids):
    """Refresh accepted source snapshots twice using finite intrinsic descriptors.

    First pass accepts changed intrinsic self/cycle descriptors. Second pass saves
    their final full manifests; no recursive content hash enters the descriptor.
    Original signed and issued manifests are never rewritten.
    """
    from onpf.drafting import evidence
    for _ in range(2):
        current = {source['handle']: evidence._manifest(source) for source in evidence._catalog(actor, program_id)}
        for question_id in question_ids:
            context = question_context(actor, program_id, question_id)
            manifests = []
            for source in context['sources']:
                if source['handle'] not in current:
                    raise DomainError('stale_evidence', 'A selected source became unavailable during this save.', 409)
                manifests.append(current[source['handle']])
            get_db().execute('UPDATE question_contexts SET sources=? WHERE program_id=? AND question_id=?',
                             (json.dumps(manifests, ensure_ascii=False), program_id, question_id))


def save_question_context(actor, program_id: str, question_id: str, reason: str, manifests: list[dict]) -> dict:
    with transaction():
        question_context(actor, program_id, question_id)
        service._text(reason, 'why this question is needed', 4000)
        manifests = _current_context_sources(actor, program_id, manifests)
        _persist_context(actor, program_id, question_id, reason, manifests)
        service._touch(program_id)
        _accepted_contexts(actor, program_id, [question_id])
        return question_context(actor, program_id, question_id)


def save_draft_questions(actor, program_id: str, payload: dict, expected_revision: int, *, ai_receipt: str | None = None) -> list[dict]:
    """Explicit bulk Save only: validate once, save drafts, attach origins atomically.

    Payload has questions (normal editable question fields plus reason and
    source_handles) and sources (content-free current server manifests). For AI
    saves, handles may select only the original signed receipt's sources.
    """
    from onpf.drafting import evidence, provenance
    service._record(payload)
    with transaction():
        require_role(actor, program_id, service.EDIT_ROLES)
        service._revision(program_id, expected_revision)
        questions, manifests = payload.get('questions'), payload.get('sources', [])
        if not isinstance(questions, list) or not 1 <= len(questions) <= 100 or any(not isinstance(q, dict) for q in questions):
            raise DomainError('invalid_questions', 'Save one to 100 labeled draft questions.', 422)
        identities = [q['id'] for q in questions if q.get('id') is not None]
        if any(not isinstance(key, str) for key in identities) or len(set(identities)) != len(identities):
            raise DomainError('invalid_questions', 'Save each existing question only once.', 422)
        first = questions[0]
        verified = provenance._prepare_save(actor, program_id, 'questions', ai_receipt,
            record_key=first.get('id'), stage=first.get('stage', 1), document_key=first.get('document_key', 'overview'))
        evidence.assert_current(actor, program_id, manifests)
        authorized = {source['handle']: source for source in manifests}
        if verified:
            signed = {source['handle']: source for source in verified.payload['sources']}
            if any(handle not in signed or evidence._encode(source) != evidence._encode(signed[handle])
                   for handle, source in authorized.items()):
                raise DomainError('invalid_sources', 'Use sources from this question suggestion.', 422)
        contexts = []
        for question in questions:
            reason = service._text(question.get('reason', ''), 'why this question is needed', 4000)
            handles = question.get('source_handles', [])
            evidence._handles(handles)
            if any(handle not in authorized for handle in handles):
                raise DomainError('invalid_sources', 'Choose sources from the authorized suggestion manifest.', 422)
            contexts.append((reason, [authorized[handle] for handle in handles]))
        saved = []
        for question, (reason, selected) in zip(questions, contexts):
            current_revision = get_db().execute('SELECT revision FROM programs WHERE id=?', (program_id,)).fetchone()[0]
            record = service.save_question(actor, program_id, question, current_revision)
            _persist_context(actor, program_id, record['id'], reason, selected)
            saved.append(record)
        _accepted_contexts(actor, program_id, [q['id'] for q in saved])
        for index, record in enumerate(saved):
            context = question_context(actor, program_id, record['id'])
            saved[index] = {**record, 'reason': context['reason'], 'sources': context['sources'],
                            'source_handles': [source['handle'] for source in context['sources']]}
            if verified:
                provenance._attach_verified(actor, program_id, {'kind': 'questions', 'record_key': record['id']}, verified, saved[index])
        return saved


def issue_round(actor, program_id: str, payload: dict, expected_revision: int) -> dict:
    service._record(payload)
    with transaction() as connection:
        require_role(actor, program_id, service.EDIT_ROLES)
        service._revision(program_id, expected_revision)
        stage = payload.get('stage')
        if stage is not None and (type(stage) is not int or stage not in range(1, 8)):
            raise DomainError('invalid_stage', 'Choose a clarification stage from 1 to 7.', 422)
        kind = payload.get('kind', 'clarification')
        if not isinstance(kind, str) or kind not in {'initial', 'clarification'}:
            raise DomainError('invalid_round_kind', 'Choose an initial or clarification round.', 422)
        reason = service._text(payload.get('reason', ''), 'why this round is needed', 4000)
        catalog = service.question_catalog(actor, program_id)
        selected = payload.get('question_ids', [q['id'] for q in catalog if q['relevant']])
        if not isinstance(selected, list) or any(not isinstance(key, str) for key in selected):
            raise DomainError('invalid_questions', 'Choose available questions.', 422)
        contexts = {key: question_context(actor, program_id, key) for key in selected}
        for context in contexts.values():
            context['sources'] = _current_context_sources(actor, program_id, context['sources'])
        # Default support is the exact deduplicated union of explicitly selected
        # question context sources. Additional support must be explicitly supplied.
        union = {source['handle']: source for context in contexts.values() for source in context['sources']}
        manifests = payload['sources'] if 'sources' in payload else [union[handle] for handle in sorted(union)]
        manifests = _current_context_sources(actor, program_id, manifests)
        batch = service.issue_batch(actor, program_id, payload)
        connection.execute('INSERT INTO drafting_clarification_rounds(batch_id,program_id,stage,kind,reason,sources,issued_by,issued_at) VALUES (?,?,?,?,?,?,?,?)',
                           (batch['id'], program_id, stage, kind, reason, json.dumps(manifests, ensure_ascii=False), actor.user_id, batch['issued_at']))
        for question in batch['questions']:
            context = contexts[question['source_id']]
            connection.execute('INSERT INTO clarification_round_questions(question_id,batch_id,reason,sources) VALUES (?,?,?,?)',
                (question['id'], batch['id'], context['reason'], json.dumps(context['sources'], ensure_ascii=False)))
        service._touch(program_id)
        return service.get_batch(actor, batch['id'])


def defer_question(actor, program_id: str, batch_id: str, question_id: str, reason: str, expected_revision: int) -> dict:
    with transaction() as connection:
        require_role(actor, program_id, service.EDIT_ROLES)
        service._revision(program_id, expected_revision)
        row = connection.execute('SELECT 1 FROM drafting_clarification_rounds r JOIN clarification_round_questions q '
            'ON q.batch_id=r.batch_id WHERE r.program_id=? AND r.batch_id=? AND q.question_id=?',
            (program_id, batch_id, question_id)).fetchone()
        if not row:
            raise DomainError('not_found', 'This clarification question is unavailable.', 404)
        service._text(reason, 'a reason for deferral', 4000, True)
        record = {'id': str(uuid4()), 'program_id': program_id, 'batch_id': batch_id,
                  'question_id': question_id, 'actor_id': actor.user_id, 'deferred_at': utcnow(), 'reason': reason}
        connection.execute('INSERT INTO question_deferrals(id,program_id,batch_id,question_id,actor_id,deferred_at,reason) VALUES (?,?,?,?,?,?,?)', tuple(record.values()))
        service._touch(program_id)
        return record


def rounds(actor, program_id: str) -> list[dict]:
    require_role(actor, program_id, service.EDIT_ROLES)
    from onpf.refinement.service import coverage
    responses = {}
    for response in coverage(actor, program_id):
        if response['redaction'] is None:
            responses.setdefault(response['question_id'], []).append(response)
    results = []
    for row in get_db().execute('SELECT * FROM drafting_clarification_rounds WHERE program_id=? ORDER BY issued_at,rowid', (program_id,)):
        batch = service.get_batch(actor, row['batch_id'])
        questions = []
        for question in batch['questions']:
            current = responses.get(question['id'], [])
            answered = any(r['answer_state'] in {'answered', 'abstain', 'not_applicable'} for r in current)
            deferrals = [dict(d) for d in get_db().execute('SELECT * FROM question_deferrals WHERE program_id=? AND batch_id=? AND question_id=? ORDER BY deferred_at,rowid',
                                                         (program_id, batch['id'], question['id']))]
            # Presence of differing current answers is a review flag, not an
            # automatic disposition. Prior classifications remain in coverage.
            conflicting = len({r['text'] for r in current if r['answer_state'] == 'answered'}) > 1
            questions.append({**question, 'responses': current, 'answered': answered,
                              'state': 'answered' if answered else 'deferred' if deferrals else 'pending',
                              'deferrals': deferrals, 'conflicting': conflicting,
                              'review_required': any(r['review_required'] or r['status'] == 'unreviewed' for r in current)})
        results.append({**batch, 'stage': row['stage'], 'questions': questions,
                        'pending_count': sum(not q['answered'] for q in questions),
                        'answered_count': sum(q['answered'] for q in questions),
                        'deferred_count': sum(bool(q['deferrals']) for q in questions),
                        'complete': all(q['answered'] for q in questions)})
    return results
