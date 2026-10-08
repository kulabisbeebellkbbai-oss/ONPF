"""Production refinement and releases compose with reviewed AI suggestions."""
import pytest

from onpf.db import get_db
from onpf.errors import DomainError
from onpf.inquiries import clarifications, service as inquiries
from onpf.programs.service import get_program
from onpf.releases.service import get_candidate, prepare_candidate


def _revision(owner, program):
    return get_program(owner, program['id'])['revision']


def _legacy_round(owner, program):
    return inquiries.issue_batch(owner, program['id'], {
        'question_ids': ['core-1-1'], 'title': 'Existing production clarification',
        'clarification': {'after_step': 'after_proposals', 'purpose': 'Confirm site access',
            'sources': {'core-1-1': {'source_type': 'program', 'source_id': program['id'],
                                   'why': 'Access is unresolved', 'group_label': 'Site'}}}})


def test_both_round_styles_gate_candidates_and_preserve_frozen_history(owner, program):
    legacy = _legacy_round(owner, program)
    inquiries.defer_clarification_question(owner, legacy['questions'][0]['id'], 'Await access')
    prepared = prepare_candidate(owner, program['id'], _revision(owner, program),
                                 clarifications_reviewed=True)
    frozen = get_db().execute('SELECT snapshot FROM release_candidates WHERE id=?',
                             (prepared['id'],)).fetchone()[0]
    drafted = clarifications.issue_round(owner, program['id'], {
        'question_ids': ['core-1-1'], 'stage': 4, 'reason': 'Resolve updated access'},
        _revision(owner, program))
    assert get_candidate(owner, prepared['id'])['stale']
    with pytest.raises(DomainError) as denied:
        prepare_candidate(owner, program['id'], _revision(owner, program),
                          clarifications_reviewed=True)
    assert denied.value.code == 'pending_clarification'
    clarifications.defer_question(owner, program['id'], drafted['id'],
        drafted['questions'][0]['id'], 'Await confirmation', _revision(owner, program))
    with pytest.raises(DomainError) as denied:
        prepare_candidate(owner, program['id'], _revision(owner, program))
    assert denied.value.code == 'clarification_review_required'
    current = prepare_candidate(owner, program['id'], _revision(owner, program),
                                clarifications_reviewed=True)
    assert {row['id'] for row in current['clarification_rounds']} == {legacy['id'], drafted['id']}
    assert all(row['deferred'] == 1 and row['pending'] == 0 for row in current['clarification_rounds'])
    assert get_db().execute('SELECT snapshot FROM release_candidates WHERE id=?',
                           (prepared['id'],)).fetchone()[0] == frozen


def test_generated_decision_preserves_proposal_snapshot_and_incorporation_history(owner, program, submitted):
    from onpf.drafting import evidence, provenance
    from onpf.refinement.service import get_proposal, list_decisions, save_proposal, record_decision
    response_id = submitted['response_ids'][0]
    option = save_proposal(owner, program['id'], {
        'title': 'Original option', 'text': 'Original reviewed wording',
        'response_ids': [response_id]}, None)
    handles = [source['handle'] for source in evidence.catalog(owner, program['id'],
                {'kind': 'decision'}) if source['kind'] == 'proposal']
    manifests = evidence.select(owner, program['id'], {'kind': 'decision'}, handles)['sources']
    fields = {'outcome': 'Reviewed choice', 'rationale': 'Owner reason', 'proposal_ids': [option['id']]}
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'decision'}, manifests,
                                     {'fields': fields})
    decision = record_decision(owner, program['id'], fields, ai_receipt=receipt)
    save_proposal(owner, program['id'], {'id': option['id'], 'title': 'Later option',
        'text': 'Later wording', 'response_ids': []}, option['revision'])
    captured = list_decisions(owner, program['id'])[0]['proposal_snapshots'][0]
    assert captured['title'] == 'Original option' and captured['text'] == 'Original reviewed wording'
    assert captured['proposal_revision'] == 1 and captured['capture_basis'] == 'at_decision'
    assert decision['response_ids'] == [response_id]
    assert get_proposal(owner, program['id'], option['id'])['response_ids'] == []
    assert get_db().execute('SELECT COUNT(*) FROM proposal_incorporations WHERE proposal_id=?',
                           (option['id'],)).fetchone()[0] == 1
    origin = provenance.origins(owner, program['id'], {'kind': 'decision', 'record_key': decision['id']})[0]
    assert origin['stale_sources'] == handles


def test_stale_receipt_performs_no_refinement_side_writes(owner, program, submitted):
    from onpf.drafting import evidence, provenance
    from onpf.refinement.service import save_proposal
    option = save_proposal(owner, program['id'], {
        'title': 'Option', 'text': 'Reviewed wording', 'response_ids': submitted['response_ids']}, None)
    target = {'kind': 'proposal', 'record_key': option['id']}
    manifests = evidence.select(owner, program['id'], target, [])['sources']
    receipt = provenance.issue_receipt(owner, program['id'], target, manifests,
                                     {'fields': {'title': 'Generated', 'text': 'Generated'}})
    inquiries.save_question(owner, program['id'], {'text': 'Later question'}, _revision(owner, program))
    tables = ('proposals', 'proposal_responses', 'proposal_incorporations', 'responses', 'programs', 'ai_origins')
    before = {table: [tuple(row) for row in get_db().execute(f'SELECT * FROM {table} ORDER BY rowid')]
              for table in tables}
    with pytest.raises(DomainError) as denied:
        save_proposal(owner, program['id'], {'id': option['id'], 'title': 'Generated',
            'text': 'Generated', 'response_ids': []}, option['revision'], ai_receipt=receipt)
    assert denied.value.code == 'stale_evidence'
    assert {table: [tuple(row) for row in get_db().execute(f'SELECT * FROM {table} ORDER BY rowid')]
            for table in tables} == before
