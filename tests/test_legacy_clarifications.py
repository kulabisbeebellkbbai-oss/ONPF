"""Repeatable, scoped clarification rounds preserve question and answer history."""
import pytest

from onpf.errors import DomainError


def _round(question_id, program_id, *, after_step='after_proposals', source_type='program', source_id=None):
    return {'title': 'Fictional clarification', 'question_ids': [question_id], 'clarification': {
        'after_step': after_step, 'purpose': 'Resolve a fictional missing detail',
        'sources': {question_id: {'source_type': source_type, 'source_id': source_id or program_id,
                                  'why': 'This detail is not yet confirmed.', 'group_label': 'Site details'}},
    }}


def test_repeatable_rounds_track_answers_and_deferrals(owner, program):
    from onpf.contributions.service import submit_responses
    from onpf.db import get_db
    from onpf.inquiries.service import (clarification_overview, defer_clarification_question,
                                        get_batch, issue_batch, save_question)

    question = save_question(owner, program['id'], {'text': 'Who confirms fictional site access?', 'stage': 4,
                        'answer_type': 'text', 'document_key': 'delivery', 'depends_on': []}, program['revision'])
    first = issue_batch(owner, program['id'], _round(question['id'], program['id']))
    assert first['clarification']['round_number'] == 1
    assert first['questions'][0]['clarification_source']['why'] == 'This detail is not yet confirmed.'
    first_question_id = first['questions'][0]['id']
    assert clarification_overview(owner, program['id'])[0]['pending'] == 1
    submitted = submit_responses(owner, first['id'], 'fictional-round-1', [
        {'question_id': first_question_id, 'text': 'Fictional Bob will confirm.', 'attribution': 'anonymous'}])
    assert clarification_overview(owner, program['id'])[0]['answered'] == 1

    second = issue_batch(owner, program['id'], _round(question['id'], program['id'],
                         after_step='document_review', source_type='response', source_id=submitted['response_ids'][0]))
    assert second['clarification']['round_number'] == 2
    assert second['questions'][0]['clarification_source']['source_id'] == submitted['response_ids'][0]
    defer_clarification_question(owner, second['questions'][0]['id'], 'Waiting for a fictional site check.')
    overview = clarification_overview(owner, program['id'])
    assert [(row['answered'], row['deferred'], row['pending']) for row in overview] == [(1, 0, 0), (0, 1, 0)]
    assert get_batch(owner, first['id'])['questions'][0]['text'] == 'Who confirms fictional site access?'
    assert get_db().execute('SELECT COUNT(*) FROM decisions').fetchone()[0] == 0


def test_clarification_source_must_belong_to_program(owner, program):
    from onpf.inquiries.service import issue_batch
    with pytest.raises(DomainError) as error:
        issue_batch(owner, program['id'], _round('core-1-1', program['id'], source_type='response', source_id='other-project'))
    assert error.value.code == 'invalid_source'


def test_round_form_and_sidebar_are_available_without_submitting(login_client, program):
    root = f"/programs/{program['id']}"
    form = login_client.get(root + '/batches/new?round=1')
    assert form.status_code == 200
    assert b'Compose clarification round' in form.data
    assert b'Issue frozen clarification round' in form.data
    workspace = login_client.get(root)
    assert b'Workflow walkthrough' in workspace.data
    assert b'Start another clarification round' in workspace.data


def test_pending_and_deferred_rounds_control_candidate_preparation(owner, program, login_client):
    from onpf.inquiries.service import defer_clarification_question, issue_batch
    from onpf.programs.service import get_program
    from onpf.releases.service import get_candidate, prepare_candidate

    before = prepare_candidate(owner, program['id'], program['revision'])
    issued = issue_batch(owner, program['id'], _round('core-1-1', program['id']))
    assert get_candidate(owner, before['id'])['stale'] is True
    revision = get_program(owner, program['id'])['revision']
    with pytest.raises(DomainError) as error:
        prepare_candidate(owner, program['id'], revision)
    assert error.value.code == 'pending_clarification'
    question_id = issued['questions'][0]['id']
    defer_clarification_question(owner, question_id, 'Fictional reason for a later cycle.')
    revision = get_program(owner, program['id'])['revision']
    with pytest.raises(DomainError) as error:
        prepare_candidate(owner, program['id'], revision)
    assert error.value.code == 'clarification_review_required'
    page = login_client.get(f"/programs/{program['id']}/releases")
    assert 'Round 1' in page.text and '1 deferred' in page.text
    prepared = prepare_candidate(owner, program['id'], revision, clarifications_reviewed=True)
    assert prepared['clarification_rounds'][0]['deferred'] == 1
