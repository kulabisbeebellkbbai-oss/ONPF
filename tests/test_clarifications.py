import json
import sqlite3

import pytest

from onpf.db import get_db
from onpf.errors import DomainError
from onpf.inquiries import service
from onpf.programs.service import get_program, create_program
from onpf.drafting import evidence, provenance


def clarifications():
    from onpf.inquiries import clarifications as module
    return module


def revision(owner, program):
    return get_program(owner, program['id'])['revision']


def sources(owner, program):
    return evidence.select(owner, program['id'], {'kind': 'questions'},
                           [f'program:{program["id"]}:{program["id"]}'])['sources']


def test_later_rounds_preserve_sources_answers_and_unanswered_state(owner, program):
    from onpf.contributions.service import submit_responses, revise_response
    module = clarifications()
    ordinary = service.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})
    issued = []
    for stage in (2, 4, 5, 7):
        question = service.save_question(owner, program['id'], {
            'text': f'Clarify stage {stage}', 'stage': stage,
            'document_key': 'overview'}, revision(owner, program))
        context = module.save_question_context(owner, program['id'], question['id'],
                                               f'Reason at stage {stage}', sources(owner, program))
        batch = module.issue_round(owner, program['id'], {'stage': stage, 'title': f'Round {stage}',
            'question_ids': [question['id']]}, revision(owner, program))
        issued.append((batch, question, context))
    first, question, context = issued[0]
    response = submit_responses(owner, first['id'], 'clarification-answer', [
        {'question_id': first['questions'][0]['id'], 'text': 'First view', 'attribution': 'anonymous'}])
    response_id = response['response_ids'][0]
    revise_response(owner, response_id, 'Corrected view', 'Requested correction', 1)
    module.save_question_context(owner, program['id'], question['id'], 'New draft reason', [])
    service.save_question(owner, program['id'], {'id': question['id'], 'text': 'Later wording'}, revision(owner, program))
    service.close_batch(owner, issued[1][0]['id'])
    rows = module.rounds(owner, program['id'])
    assert ordinary['id'] not in {row['id'] for row in rows}
    assert sorted(row['version'] for row in rows) == [2, 3, 4, 5]
    frozen = next(row for row in rows if row['id'] == first['id'])['questions'][0]
    assert frozen['text'] == 'Clarify stage 2'
    assert frozen['reason'] == 'Reason at stage 2' and frozen['sources'] == context['sources']
    assert frozen['answered'] and frozen['state'] == 'answered'
    assert frozen['responses'][0]['text'] == 'Corrected view' and frozen['review_required']
    closed = next(row for row in rows if row['id'] == issued[1][0]['id'])
    assert closed['closed_at'] and closed['questions'][0]['state'] == 'pending'
    assert not closed['complete'] and closed['pending_count'] == 1


def test_deferral_is_explicit_append_only_and_later_answer_visible(owner, program):
    from onpf.contributions.service import submit_responses
    module = clarifications()
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    question_id = batch['questions'][0]['id']
    deferred = module.defer_question(owner, program['id'], batch['id'], question_id,
                                     'Await site permission', revision(owner, program))
    assert deferred['actor_id'] == owner.user_id and deferred['deferred_at']
    q = module.rounds(owner, program['id'])[0]['questions'][0]
    assert q['state'] == 'deferred' and not q['answered']
    submit_responses(owner, batch['id'], 'later-answer', [
        {'question_id': question_id, 'text': 'Permission pending', 'attribution': 'anonymous'}])
    q = module.rounds(owner, program['id'])[0]['questions'][0]
    assert q['state'] == 'answered' and q['deferrals'][0]['reason'] == 'Await site permission'
    assert q['review_required'] and q['responses'][0]['text'] == 'Permission pending'
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('DELETE FROM question_deferrals WHERE id=?', (deferred['id'],))


def test_removed_answer_is_excluded_and_current_abstention_counts(owner, program):
    from onpf.contributions.service import submit_responses
    from onpf.archives.service import redact_response
    module = clarifications()
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    receipt = submit_responses(owner, batch['id'], 'removed-answer', [
        {'question_id': batch['questions'][0]['id'], 'text': 'Remove this view', 'attribution': 'anonymous'},
        {'question_id': batch['questions'][0]['id'], 'text': '', 'answer_state': 'abstain', 'attribution': 'anonymous'}])
    redact_response(owner, receipt['response_ids'][0], 'privacy_request')
    row = module.rounds(owner, program['id'])[0]
    assert row['complete'] and row['questions'][0]['answered']
    assert [r['answer_state'] for r in row['questions'][0]['responses']] == ['abstain']
    assert not row['questions'][0]['conflicting']
    assert all(r['text'] != 'Remove this view' for r in row['questions'][0]['responses'])


def test_context_sources_reauthorize_and_freeze_at_issue(owner, other_owner, program):
    module = clarifications()
    other = create_program(other_owner, {'title': 'Other'})
    foreign = sources(other_owner, other)
    with pytest.raises(DomainError):
        module.save_question_context(owner, program['id'], 'core-1-1', 'A reason', foreign)
    with pytest.raises(DomainError) as forbidden:
        module.save_question_context(other_owner, program['id'], 'core-1-1', 'A reason', [])
    assert forbidden.value.status == 403
    module.save_question_context(owner, program['id'], 'core-1-1', 'A reason', sources(owner, program))
    # Saving context touches the aggregate revision. It must reconcile its own
    # program manifest, while a later independent program change makes it stale.
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, revision(owner, program))
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('UPDATE clarification_round_questions SET reason=? WHERE batch_id=?', ('Changed', batch['id']))
    module.save_question_context(owner, program['id'], 'core-1-1', 'Another reason', sources(owner, program))
    from onpf.programs.service import update_program
    update_program(owner, program['id'], {'purpose': 'Changed context'}, revision(owner, program))
    with pytest.raises(DomainError) as stale:
        module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, revision(owner, program))
    assert stale.value.code == 'stale_evidence'
    assert len(service.list_batches(owner, program['id'])) == 1


def test_issue_revision_rollback_and_invitation_scope(owner, program, monkeypatch):
    module = clarifications()
    with pytest.raises(DomainError) as stale:
        module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 0)
    assert stale.value.code == 'stale_revision'
    with pytest.raises(DomainError):
        module.issue_round(owner, program['id'], {'question_ids': ['core-1-1'], 'stage': 8}, 1)
    assert not service.list_batches(owner, program['id'])
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    invitee = service.resolve_invitation(service.create_invitation(owner, batch['id']))
    assert 'reason' not in service.get_batch(invitee, batch['id'])['questions'][0]
    for operation in (lambda: module.rounds(invitee, program['id']),
                      lambda: module.defer_question(invitee, program['id'], batch['id'], batch['questions'][0]['id'], 'Private', revision(owner, program))):
        with pytest.raises(DomainError) as denied:
            operation()
        assert denied.value.status == 403
    original = service.issue_batch
    def fail_after_issue(*args, **kwargs):
        original(*args, **kwargs)
        raise DomainError('test_rollback', 'Rollback', 422)
    monkeypatch.setattr(service, 'issue_batch', fail_after_issue)
    with pytest.raises(DomainError):
        module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, revision(owner, program))
    assert len(service.list_batches(owner, program['id'])) == 1


def test_bulk_questions_save_only_and_origin_context_traceability(owner, program, monkeypatch):
    module = clarifications()
    selected = sources(owner, program)
    question = {'text': 'Generated question', 'stage': 2, 'document_key': 'overview',
                'answer_type': 'text', 'reason': 'Generated reason', 'source_handles': [selected[0]['handle']]}
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'questions'}, selected,
                                       {'fields': {'questions': [question]}, 'sources': selected})
    calls = []
    original = provenance._prepare_save
    def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(provenance, '_prepare_save', prepare)
    payload = {'questions': [{**question, 'reason': 'Reviewed reason'}, {**question, 'text': 'Second question', 'source_handles': []}], 'sources': selected}
    saved = module.save_draft_questions(owner, program['id'], payload, 1, ai_receipt=receipt)
    assert len([call for call in calls if call[0][3] is not None]) == 1
    assert not service.list_batches(owner, program['id']) and len(saved) == 2
    origin = provenance.origins(owner, program['id'], {'kind': 'questions', 'record_key': saved[0]['id']})[0]
    assert not origin['stale']
    assert origin['reviewed_hash'] == evidence._hash(provenance._reviewed_fields('questions', saved[0]))
    assert saved[0]['reason'] == 'Reviewed reason' and saved[0]['sources'] == module.question_context(owner, program['id'], saved[0]['id'])['sources']
    module.save_question_context(owner, program['id'], saved[0]['id'], 'Later reason', [])
    assert provenance.origins(owner, program['id'], {'kind': 'questions', 'record_key': saved[0]['id']})[0]['target_changed']


def test_bulk_rejects_unbound_sources_wrong_kind_and_rolls_back(owner, program, monkeypatch):
    module = clarifications()
    selected = sources(owner, program)
    question = {'text': 'Question', 'stage': 1, 'document_key': 'overview', 'answer_type': 'text', 'reason': '', 'source_handles': []}
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, selected, {'fields': {'title': 'Title'}})
    with pytest.raises(DomainError) as wrong:
        module.save_draft_questions(owner, program['id'], {'questions': [question], 'sources': selected}, 1, ai_receipt=receipt)
    assert wrong.value.code == 'invalid_ai_receipt'
    unknown = {**question, 'source_handles': ['question:unknown:unknown']}
    with pytest.raises(DomainError):
        module.save_draft_questions(owner, program['id'], {'questions': [question, unknown], 'sources': selected}, 1)
    assert not get_db().execute('SELECT 1 FROM inquiry_questions').fetchone()
    saved = module.save_draft_questions(owner, program['id'], {'questions': [question], 'sources': []}, 1)
    assert saved and not get_db().execute('SELECT 1 FROM ai_origins').fetchone()


def test_round_forms_and_context_route(login_client, owner, program, csrf_token):
    module = clarifications()
    path = f'/programs/{program["id"]}/rounds/new'
    page = login_client.get(path)
    assert page.status_code == 200 and 'Issue clarification round' in page.text
    assert 'Round kind' in page.text and 'Why this round is needed' in page.text
    response = login_client.post(path, data={'csrf_token': csrf_token(login_client, path),
        'expected_revision': 1, 'question_ids': ['core-1-1'], 'title': 'Later round', 'stage': '7'})
    assert response.status_code == 302
    detail = login_client.get(response.location)
    assert 'Defer question' in detail.text and 'Pending' in detail.text
    assert 'Clarification rounds' in login_client.get(f'/programs/{program["id"]}').text
    context_path = f'/programs/{program["id"]}/questions/core-1-1/context'
    posted = login_client.post(context_path, data={'csrf_token': csrf_token(login_client, path),
        'expected_revision': revision(owner, program), 'reason': 'Need details', 'sources': '[]'})
    assert posted.status_code == 302
    assert module.question_context(owner, program['id'], 'core-1-1')['reason'] == 'Need details'


def test_all_stages_and_unlimited_later_rounds(owner, program):
    module = clarifications()
    for index in range(12):
        module.issue_round(owner, program['id'], {'question_ids': ['core-1-1'], 'stage': index % 7 + 1}, revision(owner, program))
    assert [r['version'] for r in module.rounds(owner, program['id'])] == list(range(1, 13))
    assert all(not r['complete'] for r in module.rounds(owner, program['id']))


def test_conflicts_and_revision_bound_dispositions_survive_deferral(owner, program):
    from onpf.contributions.service import submit_responses, revise_response
    from onpf.refinement.service import set_disposition
    module = clarifications()
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    question_id = batch['questions'][0]['id']
    submitted = submit_responses(owner, batch['id'], 'conflicting-views', [
        {'question_id': question_id, 'text': 'Choose garden', 'attribution': 'anonymous'},
        {'question_id': question_id, 'text': 'Choose hall', 'attribution': 'anonymous'}])
    response_id = submitted['response_ids'][0]
    set_disposition(owner, response_id, 'deferred', 'Keep both perspectives', [], expected_revision=revision(owner, program))
    revise_response(owner, response_id, 'Choose larger garden', 'Requested correction', 1)
    module.defer_question(owner, program['id'], batch['id'], question_id, 'Await owner review', revision(owner, program))
    question = module.rounds(owner, program['id'])[0]['questions'][0]
    assert question['answered'] and question['conflicting'] and question['review_required']
    corrected = next(r for r in question['responses'] if r['id'] == response_id)
    assert corrected['status'] == 'unreviewed' and corrected['disposition']['status'] == 'deferred'
    assert corrected['disposition']['response_revision_id'] != corrected['current_revision_id']


def test_manual_edit_preserves_context_and_self_selection_has_no_recursion(owner, program):
    module = clarifications()
    manifest = evidence.select(owner, program['id'], {'kind': 'questions'},
                               [f'question:{program["id"]}:core-1-1'])['sources']
    module.save_question_context(owner, program['id'], 'core-1-1', 'Self context', manifest)
    context = module.question_context(owner, program['id'], 'core-1-1')
    evidence.assert_current(owner, program['id'], context['sources'])
    signed = provenance.issue_receipt(owner, program['id'], {'kind': 'questions', 'record_key': 'core-1-1'},
                                       context['sources'], {'fields': {'questions': []}})
    saved = service.save_question(owner, program['id'], {'id': 'core-1-1', 'text': 'Edited wording'}, revision(owner, program), ai_receipt=signed)
    assert saved['reason'] == 'Self context'
    assert not provenance.origins(owner, program['id'], {'kind': 'questions', 'record_key': 'core-1-1'})[0]['stale']


def test_known_quarantined_context_and_deferral_text_never_enters_evidence(owner, program):
    from onpf.contributions.service import submit_responses
    from onpf.refinement.service import save_proposal
    from onpf.archives.service import redact_response
    module = clarifications()
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    copied = 'Private copied perspective'
    response = submit_responses(owner, batch['id'], 'quarantine-context', [
        {'question_id': batch['questions'][0]['id'], 'text': copied, 'attribution': 'anonymous'}])
    save_proposal(owner, program['id'], {'title': 'Copied input', 'text': copied}, None)
    module.save_question_context(owner, program['id'], 'core-1-1', copied, [])
    module.defer_question(owner, program['id'], batch['id'], batch['questions'][0]['id'], copied, revision(owner, program))
    before = evidence.select(owner, program['id'], {'kind': 'review'},
                             [f'issued_question:{program["id"]}:{batch["questions"][0]["id"]}'])['sources']
    redact_response(owner, response['response_ids'][0], 'privacy_request')
    available = evidence.catalog(owner, program['id'], {'kind': 'review'})
    assert copied not in json.dumps(available)
    assert not any(s['kind'] == 'question' and s['record_key'] == 'core-1-1' for s in available)
    with pytest.raises(DomainError) as stale:
        evidence.assert_current(owner, program['id'], before)
    assert stale.value.code == 'stale_evidence'
    # The owner can still select clean replacement support while repairing context.
    assert module.context_options(owner, program['id'], 'core-1-1')


def test_deferral_changes_selected_evidence_and_requires_current_revision(owner, program):
    module = clarifications()
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    source = evidence.select(owner, program['id'], {'kind': 'questions'},
        [f'issued_question:{program["id"]}:{batch["questions"][0]["id"]}'])['sources']
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'questions'}, source,
                                       {'fields': {'questions': []}})
    module.defer_question(owner, program['id'], batch['id'], batch['questions'][0]['id'], 'Pending details', revision(owner, program))
    with pytest.raises(DomainError) as stale:
        provenance.validate_receipt(owner, program['id'], {'kind': 'questions'}, receipt)
    assert stale.value.code == 'stale_evidence'
    with pytest.raises(DomainError) as stale:
        module.defer_question(owner, program['id'], batch['id'], batch['questions'][0]['id'], 'Another reason', 1)
    assert stale.value.code == 'stale_revision'
    assert len(module.rounds(owner, program['id'])[0]['questions'][0]['deferrals']) == 1


def test_bulk_attachment_failure_and_invalid_later_question_rollback(owner, program, monkeypatch):
    module = clarifications()
    question = {'text': 'Question', 'stage': 1, 'document_key': 'overview', 'answer_type': 'text', 'reason': '', 'source_handles': []}
    for payload in ({'questions': [question, {**question, 'stage': 8}]},
                    {'questions': [{**question, 'id': 'core-1-1'}, {**question, 'id': 'core-1-1'}]}):
        with pytest.raises(DomainError):
            module.save_draft_questions(owner, program['id'], payload, 1)
        assert not get_db().execute('SELECT 1 FROM inquiry_questions').fetchone()
        assert revision(owner, program) == 1
    signed = provenance.issue_receipt(owner, program['id'], {'kind': 'questions'}, [], {'fields': {'questions': [question]}})
    def fail(*args, **kwargs):
        raise DomainError('test_rollback', 'Rollback', 422)
    monkeypatch.setattr(provenance, '_attach_verified', fail)
    with pytest.raises(DomainError):
        module.save_draft_questions(owner, program['id'], {'questions': [question]}, 1, ai_receipt=signed)
    assert not get_db().execute('SELECT 1 FROM inquiry_questions').fetchone()
    assert not get_db().execute('SELECT 1 FROM question_contexts').fetchone()
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()
    assert revision(owner, program) == 1


def test_bulk_handles_must_belong_to_signed_manifest(owner, program):
    module = clarifications()
    selected = sources(owner, program)
    question = {'text': 'Question', 'stage': 1, 'document_key': 'overview', 'answer_type': 'text', 'reason': '', 'source_handles': []}
    signed = provenance.issue_receipt(owner, program['id'], {'kind': 'questions'}, [], {'fields': {'questions': [question]}})
    with pytest.raises(DomainError) as invalid:
        module.save_draft_questions(owner, program['id'], {'questions': [question], 'sources': selected}, 1, ai_receipt=signed)
    assert invalid.value.code == 'invalid_sources'
    for invalid_receipt in ({'target': {'kind': 'questions'}}, ''):
        with pytest.raises(DomainError) as invalid:
            module.save_draft_questions(owner, program['id'], {'questions': [question]}, 1, ai_receipt=invalid_receipt)
        assert invalid.value.code == 'invalid_ai_receipt'
    assert revision(owner, program) == 1


def test_manual_bulk_form_normalizes_empty_receipt(login_client, program, csrf_token):
    path = f'/programs/{program["id"]}/questions/save-drafts'
    compose = f'/programs/{program["id"]}/batches/new'
    posted = login_client.post(path, data={'csrf_token': csrf_token(login_client, compose),
        'expected_revision': '1', 'ai_receipt': '', 'questions': json.dumps([{
            'text': 'Manual question', 'stage': 1, 'document_key': 'overview', 'answer_type': 'text',
            'reason': 'Manual context', 'source_handles': []}]), 'sources': '[]'})
    assert posted.status_code == 302 and '/rounds/new' in posted.location
    assert not get_db().execute('SELECT 1 FROM batches').fetchone()
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()


def test_schema_rejects_mismatched_question_batch_and_program(owner, program, other_owner):
    module = clarifications()
    first = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    other_program = create_program(other_owner, {'title': 'Other program'})
    other = module.issue_round(other_owner, other_program['id'], {'question_ids': ['core-1-1']}, 1)
    ordinary = service.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('INSERT INTO clarification_round_questions(question_id,batch_id,reason,sources) VALUES (?,?,?,?)',
                         (ordinary['questions'][0]['id'], first['id'], 'Wrong batch', '[]'))
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('INSERT INTO question_deferrals(id,program_id,batch_id,question_id,actor_id,deferred_at,reason) VALUES (?,?,?,?,?,?,?)',
            ('mismatched-deferral', program['id'], other['id'], first['questions'][0]['id'], owner.user_id, 'now', 'Wrong scope'))


@pytest.mark.parametrize('answer_state', ['answered', 'abstain', 'not_applicable'])
def test_every_valid_current_response_counts_and_removed_response_does_not(owner, program, answer_state):
    from onpf.contributions.service import submit_responses
    from onpf.archives.service import redact_response
    module = clarifications()
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, 1)
    assert not module.rounds(owner, program['id'])[0]['complete']
    receipt = submit_responses(owner, batch['id'], 'valid-kind-' + answer_state, [{
        'question_id': batch['questions'][0]['id'], 'text': 'A substantive response' if answer_state == 'answered' else '',
        'answer_state': answer_state, 'attribution': 'anonymous'}])
    row = module.rounds(owner, program['id'])[0]
    assert row['complete'] and row['answered_count'] == 1 and row['pending_count'] == 0
    question = row['questions'][0]
    assert question['state'] == 'answered' and not question['conflicting']
    assert question['responses'][0]['answer_state'] == answer_state and question['review_required']
    redact_response(owner, receipt['response_ids'][0], 'privacy_request')
    row = module.rounds(owner, program['id'])[0]
    assert not row['complete'] and row['answered_count'] == 0 and row['pending_count'] == 1
    assert not row['questions'][0]['responses']


def test_round_level_kind_reason_sources_are_frozen_current_and_scoped(owner, program, other_owner):
    module = clarifications()
    module.save_question_context(owner, program['id'], 'core-1-1', 'Question reason', sources(owner, program))
    context = module.question_context(owner, program['id'], 'core-1-1')
    initial = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1'], 'kind': 'initial',
        'reason': 'Establish the initial questions', 'stage': 1}, revision(owner, program))
    assert initial['kind'] == 'initial' and initial['reason'] == 'Establish the initial questions'
    assert initial['sources'] == context['sources']
    assert module.rounds(owner, program['id'])[0]['kind'] == 'initial'
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('UPDATE drafting_clarification_rounds SET reason=? WHERE batch_id=?', ('Changed', initial['id']))
    other = create_program(other_owner, {'title': 'Other program'})
    for invalid in ({'kind': 'other'}, {'reason': 3}, {'sources': sources(other_owner, other)},
                    {'sources': [{**context['sources'][0], 'content_hash': '0' * 64}]}):
        with pytest.raises(DomainError):
            module.issue_round(owner, program['id'], {'question_ids': ['core-4-3'], **invalid}, revision(owner, program))
    assert len(service.list_batches(owner, program['id'])) == 1
    later = module.issue_round(owner, program['id'], {'question_ids': ['core-4-3'],
        'reason': 'Review delivery', 'sources': sources(owner, program)}, revision(owner, program))
    assert later['kind'] == 'clarification' and later['sources']
    invitee = service.resolve_invitation(service.create_invitation(owner, later['id']))
    assert not {'kind', 'reason', 'sources', 'stage'}.intersection(service.get_batch(invitee, later['id']))


def test_round_reason_is_evidence_and_known_quarantine_changes_fingerprint(owner, program):
    from onpf.contributions.service import submit_responses
    from onpf.refinement.service import save_proposal
    from onpf.archives.service import redact_response
    module = clarifications()
    copied = 'Round copied private input'
    batch = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1'],
        'kind': 'clarification', 'reason': copied}, 1)
    available = evidence.catalog(owner, program['id'], {'kind': 'review'})
    batch_source = next(s for s in available if s['kind'] == 'batch' and s['record_key'] == batch['id'])
    assert batch_source['content']['reason'] == copied and batch_source['content']['round_kind'] == 'clarification'
    assert 'sources' not in batch_source['content']
    selected = [evidence._manifest(batch_source)]
    submitted = submit_responses(owner, batch['id'], 'round-context-removal', [{
        'question_id': batch['questions'][0]['id'], 'text': copied, 'attribution': 'anonymous'}])
    save_proposal(owner, program['id'], {'title': 'Copied round reason', 'text': copied}, None)
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert copied not in json.dumps(evidence.catalog(owner, program['id'], {'kind': 'review'}))
    with pytest.raises(DomainError) as stale:
        evidence.assert_current(owner, program['id'], selected)
    assert stale.value.code == 'stale_evidence'


def test_round_source_default_is_exact_deduplicated_selected_context_union(owner, program):
    module = clarifications()
    selected = sources(owner, program)
    question = {'text': 'First question', 'stage': 1, 'document_key': 'overview', 'answer_type': 'text',
                'reason': 'Question context', 'source_handles': [selected[0]['handle']]}
    drafts = module.save_draft_questions(owner, program['id'], {'questions': [question, {**question, 'text': 'Second question'}],
                                                                'sources': selected}, 1)
    batch = module.issue_round(owner, program['id'], {'question_ids': [q['id'] for q in drafts]}, revision(owner, program))
    assert batch['sources'] == drafts[0]['sources'] == drafts[1]['sources']
    assert len(batch['sources']) == 1


def aggregate_context_source(owner, program, kind):
    if kind == 'decision_field':
        service.set_decision_field(owner, program['id'], {'key': 'venue', 'label': 'Venue', 'value': 'garden'}, revision(owner, program))
    key = program['id'] if kind == 'program' else 'overview' if kind == 'document' else 'venue'
    return evidence.select(owner, program['id'], {'kind': 'questions'}, [f'{kind}:{program["id"]}:{key}'])['sources']


@pytest.mark.parametrize('kind', ['program', 'document', 'decision_field'])
def test_sequential_manual_context_saves_issue_shared_support_and_later_round(owner, program, kind):
    module = clarifications()
    original = aggregate_context_source(owner, program, kind)
    signed = provenance.issue_receipt(owner, program['id'], {'kind': 'questions'}, original, {'fields': {'questions': []}})
    module.save_question_context(owner, program['id'], 'core-1-1', 'First context', original)
    first_context = module.question_context(owner, program['id'], 'core-1-1')
    module.save_question_context(owner, program['id'], 'core-4-3', 'Second context', original)
    first = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1', 'core-4-3']}, revision(owner, program))
    assert len(first['sources']) == 1
    assert all(q['sources'] == first['sources'] for q in first['questions'])
    # Issuance normalizes only its new snapshots, preserving stored draft and
    # signed original manifests; signed receipt freshness stays exact.
    assert module.question_context(owner, program['id'], 'core-1-1')['sources'] == first_context['sources']
    assert original[0]['revision'] < first['sources'][0]['revision']
    with pytest.raises(DomainError) as stale:
        provenance.validate_receipt(owner, program['id'], {'kind': 'questions'}, signed)
    assert stale.value.code == 'stale_evidence'
    with pytest.raises(DomainError) as stale:
        module.save_draft_questions(owner, program['id'], {'questions': [{'text': 'Receipt draft', 'stage': 1,
            'document_key': 'overview', 'answer_type': 'text', 'reason': '', 'source_handles': []}],
            'sources': original}, revision(owner, program), ai_receipt=signed)
    assert stale.value.code == 'stale_evidence'
    assert not get_db().execute('SELECT 1 FROM inquiry_questions').fetchone()
    frozen = json.dumps(service.get_batch(owner, first['id'])['sources'], sort_keys=True)
    service.save_question(owner, program['id'], {'text': 'Unrelated aggregate bump'}, revision(owner, program))
    later = module.issue_round(owner, program['id'], {'question_ids': ['core-1-1', 'core-4-3'], 'sources': original}, revision(owner, program))
    assert later['version'] == first['version'] + 1
    assert later['sources'][0]['revision'] > first['sources'][0]['revision']
    assert json.dumps(service.get_batch(owner, first['id'])['sources'], sort_keys=True) == frozen


@pytest.mark.parametrize('kind', ['program', 'document', 'decision_field'])
def test_context_aggregate_tolerance_rejects_real_content_changes(owner, program, kind):
    from onpf.programs.service import update_program, save_document
    module = clarifications()
    original = aggregate_context_source(owner, program, kind)
    module.save_question_context(owner, program['id'], 'core-1-1', 'Context', original)
    if kind == 'program':
        update_program(owner, program['id'], {'purpose': 'A real change'}, revision(owner, program))
    elif kind == 'document':
        save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'A real change'}}, revision(owner, program))
    else:
        service.set_decision_field(owner, program['id'], {'key': 'venue', 'value': 'hall'}, revision(owner, program))
    for operation in (lambda: module.save_question_context(owner, program['id'], 'core-4-3', 'New context', original),
                      lambda: module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, revision(owner, program))):
        with pytest.raises(DomainError) as stale:
            operation()
        assert stale.value.code == 'stale_evidence'
    assert not service.list_batches(owner, program['id'])


@pytest.mark.parametrize('change', ['correction', 'status', 'removal'])
def test_context_response_revision_status_and_removal_still_refuse_issue(owner, program, submitted, change):
    from onpf.contributions.service import revise_response
    from onpf.refinement.service import set_disposition
    from onpf.archives.service import redact_response
    module = clarifications()
    response_id = submitted['response_ids'][0]
    selected = evidence.select(owner, program['id'], {'kind': 'questions'}, [f'response:{program["id"]}:{response_id}'])['sources']
    module.save_question_context(owner, program['id'], 'core-1-1', 'Response support', selected)
    if change == 'correction':
        revise_response(owner, response_id, 'Fictional first perspective', 'Identical text correction', 1)
    elif change == 'status':
        set_disposition(owner, response_id, 'deferred', 'Needs review', [])
    else:
        redact_response(owner, response_id, 'privacy_request')
    with pytest.raises(DomainError) as stale:
        module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, revision(owner, program))
    assert stale.value.code == 'stale_evidence'


@pytest.mark.parametrize('revision_value', [True, 1.0, -1, 999999])
def test_context_revision_tolerance_rejects_forged_revision_types_and_future(owner, program, revision_value):
    module = clarifications()
    original = sources(owner, program)
    with pytest.raises(DomainError):
        module.save_question_context(owner, program['id'], 'core-1-1', 'Context', [{**original[0], 'revision': revision_value}])


@pytest.mark.parametrize('kind', ['program', 'document'])
def test_context_aggregate_tolerance_refuses_quarantined_support(owner, program, submitted, kind):
    from onpf.programs.service import update_program, save_document
    from onpf.archives.service import redact_response
    module = clarifications()
    copied = 'Fictional first perspective'
    if kind == 'program':
        update_program(owner, program['id'], {'purpose': copied}, revision(owner, program))
    else:
        save_document(owner, program['id'], 'overview', {'sections': {'purpose': copied}}, revision(owner, program))
    original = aggregate_context_source(owner, program, kind)
    module.save_question_context(owner, program['id'], 'core-1-1', 'Private source support', original)
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    for operation in (lambda: module.save_question_context(owner, program['id'], 'core-4-3', 'Other context', original),
                      lambda: module.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, revision(owner, program))):
        with pytest.raises(DomainError) as stale:
            operation()
        assert stale.value.code == 'stale_evidence'
