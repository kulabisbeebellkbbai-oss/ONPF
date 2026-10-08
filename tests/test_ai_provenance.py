"""Explicit saves bind ephemeral suggestions to content-free origin history."""
import importlib
import json
import sqlite3
from threading import Barrier, Thread

import pytest
from itsdangerous import TimestampSigner

from onpf.auth.models import Principal
from onpf.archives.service import redact_response
from onpf.contributions.service import revise_response
from onpf.db import connect, get_db, transaction
from onpf.drafting import evidence
from onpf.errors import DomainError
from onpf.inquiries.service import save_question
from onpf.programs.service import get_program, save_document
from onpf.refinement.service import save_proposal, record_decision


@pytest.fixture
def provenance():
    if not importlib.util.find_spec('onpf.drafting.provenance'):
        pytest.fail('Explicit-save provenance service is missing')
    return importlib.import_module('onpf.drafting.provenance')


def make_receipt(provenance, owner, program, target=None, kinds=('program',), fields=None):
    target = target or {'kind': 'proposal'}
    chosen = [s['handle'] for s in evidence.catalog(owner, program['id'], target) if s['kind'] in kinds]
    manifests = evidence.select(owner, program['id'], target, chosen)['sources']
    fields = fields or {'title': 'Fictional suggested option', 'text': 'Fictional suggestion', 'theme': '', 'response_ids': []}
    output = {'fields': fields, 'sources': manifests, 'uncertainties': [], 'questions': []}
    return provenance.issue_receipt(owner, program['id'], target, manifests, output), output


def test_origin_attaches_only_on_explicit_save_and_tracks_reviewed_edits(provenance, owner, program, submitted):
    receipt, output = make_receipt(provenance, owner, program, kinds=('response',))
    assert get_db().execute('SELECT COUNT(*) FROM ai_origins').fetchone()[0] == 0
    response_id = submitted['response_ids'][0]
    saved = save_proposal(owner, program['id'], {'title': 'Reviewed option', 'text': 'Edited wording', 'response_ids': [response_id]}, None, ai_receipt=receipt)
    origins = provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']})
    assert len(origins) == 1
    origin = origins[0]
    assert origin['generated_hash'] != origin['reviewed_hash']
    assert origin['stale'] is False
    assert origin['target_changed'] is False
    assert origin['sources'] == output['sources']
    assert saved['response_ids'] == [response_id]
    encoded = json.dumps(dict(get_db().execute('SELECT * FROM ai_origins').fetchone()))
    for text in ('Fictional suggestion', 'Reviewed option', 'Edited wording', 'Fictional first perspective'):
        assert text not in encoded
    save_proposal(owner, program['id'], {'id': saved['id'], 'title': 'Manual edit', 'text': 'Manual wording'}, saved['revision'])
    later = provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']})[0]
    assert later['target_changed'] and later['stale']
    assert later['sources'] == output['sources']
    assert get_db().execute('SELECT COUNT(*) FROM ai_origins').fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['tampered', 'expired', 'actor', 'program', 'target', 'revision', 'demotion'])
def test_invalid_receipts_cannot_save_or_attach(provenance, monkeypatch, owner, facilitator, other_owner, program, failure):
    receipt, _ = make_receipt(provenance, owner, program)
    actor = owner
    target = {'kind': 'proposal'}
    if failure == 'tampered':
        prefix, signature = receipt.rsplit('.', 1)
        receipt = prefix + '.' + ('a' if signature[0] != 'a' else 'b') + signature[1:]
    elif failure == 'expired':
        timestamp = TimestampSigner.get_timestamp
        monkeypatch.setattr(TimestampSigner, 'get_timestamp', lambda self: timestamp(self) + 3601)
    elif failure == 'actor': actor = facilitator
    elif failure == 'program':
        from onpf.programs.service import create_program
        program = create_program(owner, {'title': 'Other fictional program'})
    elif failure == 'target': target = {'kind': 'questions'}
    elif failure == 'revision': save_document(owner, program['id'], 'overview', {'sections': {}}, program['revision'])
    elif failure == 'demotion': get_db().execute("UPDATE memberships SET role='contributor' WHERE program_id=? AND user_id=?", (program['id'], owner.user_id))
    before = get_db().execute('SELECT COUNT(*) FROM proposals').fetchone()[0]
    with pytest.raises(DomainError): provenance.validate_receipt(actor, program['id'], target, receipt)
    with pytest.raises(DomainError): save_proposal(actor, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt if failure != 'target' else make_receipt(provenance, owner, program, target=target)[0])
    assert get_db().execute('SELECT COUNT(*) FROM proposals').fetchone()[0] == before
    assert get_db().execute('SELECT COUNT(*) FROM ai_origins').fetchone()[0] == 0


@pytest.mark.parametrize('change', ['correction', 'removal'])
def test_saved_sources_become_stale_without_rewriting_history(provenance, owner, program, submitted, change):
    receipt, output = make_receipt(provenance, owner, program, kinds=('response',))
    saved = save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt)
    original = dict(get_db().execute('SELECT * FROM ai_origins').fetchone())
    if change == 'correction': revise_response(owner, submitted['response_ids'][0], 'Corrected answer', 'Requested correction', 1)
    else: redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    result = provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']})[0]
    assert result['stale'] and result['stale_sources']
    assert not result['target_changed']
    assert result['sources'] == output['sources']
    assert dict(get_db().execute('SELECT * FROM ai_origins').fetchone()) == original


@pytest.mark.parametrize('kind', ['questions', 'proposal', 'decision', 'document'])
def test_authorized_save_targets_and_self_mutation_remain_fresh(provenance, owner, program, kind):
    target = {'kind': kind}
    fields = {'title': 'Option', 'text': 'Wording'}
    if kind == 'questions':
        target.update(record_key='core-1-1', stage=1, document_key='overview')
        fields = {'text': 'Reviewed question?', 'stage': 1, 'document_key': 'overview', 'answer_type': 'text'}
    elif kind == 'decision': fields = {'outcome': 'Reviewed outcome', 'rationale': 'Reviewed rationale'}
    elif kind == 'document':
        target['document_key'] = 'overview'
        fields = {'sections': {}}
    kinds = ('program', 'question' if kind == 'questions' else kind)
    receipt, output = make_receipt(provenance, owner, program, target, kinds, fields)
    if kind == 'questions': saved = save_question(owner, program['id'], {'id': 'core-1-1', **fields}, program['revision'], ai_receipt=receipt)
    elif kind == 'proposal': saved = save_proposal(owner, program['id'], fields, None, ai_receipt=receipt)
    elif kind == 'decision': saved = record_decision(owner, program['id'], fields, ai_receipt=receipt)
    else: saved = save_document(owner, program['id'], 'overview', fields, program['revision'], ai_receipt=receipt)
    final_target = {'kind': kind, **({'document_key': 'overview'} if kind == 'document' else {'record_key': saved['id']})}
    origin = provenance.origins(owner, program['id'], final_target)[0]
    assert not origin['stale']
    assert origin['request_target'] == target
    assert origin['sources'] == output['sources']
    # Another save's aggregate revision bump must not invalidate unchanged content.
    save_document(owner, program['id'], 'budget', {'sections': {}}, get_program(owner, program['id'])['revision'])
    assert not provenance.origins(owner, program['id'], final_target)[0]['stale']


def test_new_question_target_identity_and_focus_are_bound(provenance, owner, program):
    target = {'kind': 'questions', 'stage': 2, 'document_key': 'charter'}
    # Use an actual available document for this framework.
    from onpf.programs.framework import load_framework
    target['document_key'] = next(iter(load_framework()['documents']))
    fields = {'text': 'New reviewed question?', 'stage': 2, 'document_key': target['document_key'], 'answer_type': 'text'}
    receipt, _ = make_receipt(provenance, owner, program, target=target)
    with pytest.raises(DomainError): save_question(owner, program['id'], {**fields, 'stage': 3}, program['revision'], ai_receipt=receipt)
    saved = save_question(owner, program['id'], fields, program['revision'], ai_receipt=receipt)
    assert saved['id']
    assert not provenance.origins(owner, program['id'], {'kind': 'questions', 'record_key': saved['id']})[0]['stale']


def test_origin_failure_rolls_back_domain_save(provenance, monkeypatch, owner, program):
    receipt, _ = make_receipt(provenance, owner, program)
    def broken(*args, **kwargs): raise RuntimeError('Fictional storage failure')
    monkeypatch.setattr(provenance, '_attach_verified', broken)
    with pytest.raises(RuntimeError): save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt)
    assert not get_db().execute('SELECT 1 FROM proposals').fetchone()
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()
    assert get_program(owner, program['id'])['revision'] == program['revision']


def test_origin_visibility_and_history_are_restricted(provenance, owner, facilitator, other_owner, program):
    receipt, _ = make_receipt(provenance, owner, program)
    saved = save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt)
    target = {'kind': 'proposal', 'record_key': saved['id']}
    assert provenance.origins(facilitator, program['id'], target)
    for actor in (other_owner, Principal(None, None, None)):
        with pytest.raises(DomainError): provenance.origins(actor, program['id'], target)
    with pytest.raises(sqlite3.IntegrityError): get_db().execute("UPDATE ai_origins SET model_alias='forged'")
    with pytest.raises(sqlite3.IntegrityError): get_db().execute('DELETE FROM ai_origins')


def test_source_change_committed_before_save_is_rejected(provenance, app, owner, program, submitted):
    receipt, _ = make_receipt(provenance, owner, program, kinds=('response',))
    errors = []
    def correction():
        try:
            with app.app_context(): revise_response(owner, submitted['response_ids'][0], 'Concurrent correction', 'Requested correction', 1)
        except BaseException as error: errors.append(error)
    worker = Thread(target=correction)
    worker.start(); worker.join(timeout=10)
    assert not worker.is_alive() and not errors
    with pytest.raises(DomainError) as denied: save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt)
    assert denied.value.code == 'stale_evidence'
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()


def test_manual_save_needs_no_drafting_configuration(provenance, owner, program):
    saved = save_proposal(owner, program['id'], {'title': 'Manual option', 'text': 'Manual wording'}, None)
    assert provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']}) == []


@pytest.mark.parametrize('change', ['correction', 'removal', 'status'])
def test_stale_source_receipt_rolls_back_before_mutation(provenance, owner, program, submitted, change):
    receipt, _ = make_receipt(provenance, owner, program, kinds=('response',))
    if change == 'correction': revise_response(owner, submitted['response_ids'][0], 'Fictional first perspective', 'Requested identical-text correction', 1)
    elif change == 'removal': redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    else:
        from onpf.refinement.service import set_disposition
        set_disposition(owner, submitted['response_ids'][0], 'deferred', 'Unresolved', [])
    with pytest.raises(DomainError) as denied:
        save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt)
    assert denied.value.code == 'stale_evidence'
    assert not get_db().execute('SELECT 1 FROM proposals').fetchone()
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()


def test_identical_text_response_revision_and_status_changes_are_stale(provenance, owner, program, submitted):
    receipt, _ = make_receipt(provenance, owner, program, kinds=('response',))
    saved = save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt)
    revise_response(owner, submitted['response_ids'][0], 'Fictional first perspective', 'Requested identical-text correction', 1)
    assert provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']})[0]['stale']


def test_receipt_cannot_move_between_existing_targets(provenance, owner, program):
    first = save_proposal(owner, program['id'], {'title': 'First', 'text': 'First wording'}, None)
    second = save_proposal(owner, program['id'], {'title': 'Second', 'text': 'Second wording'}, None)
    target = {'kind': 'proposal', 'record_key': first['id']}
    receipt, _ = make_receipt(provenance, owner, program, target=target, kinds=('proposal',))
    with pytest.raises(DomainError):
        save_proposal(owner, program['id'], {'id': second['id'], 'title': 'Moved', 'text': 'Moved wording'}, second['revision'], ai_receipt=receipt)
    saved = save_proposal(owner, program['id'], {'id': first['id'], 'title': 'Reviewed', 'text': 'Edited wording'}, first['revision'], ai_receipt=receipt)
    assert not provenance.origins(owner, program['id'], target)[0]['stale']
    assert saved['revision'] == 2


def test_scoped_decision_receipt_can_only_record_its_supersession(provenance, owner, program):
    earlier = record_decision(owner, program['id'], {'outcome': 'Earlier', 'rationale': 'Earlier rationale'})
    receipt, _ = make_receipt(provenance, owner, program, target={'kind': 'decision', 'record_key': earlier['id']}, kinds=('decision',))
    with pytest.raises(DomainError): record_decision(owner, program['id'], {'outcome': 'Later', 'rationale': 'Later rationale'}, ai_receipt=receipt)
    later = record_decision(owner, program['id'], {'outcome': 'Later', 'rationale': 'Later rationale', 'supersedes_id': earlier['id']}, ai_receipt=receipt)
    origin = provenance.origins(owner, program['id'], {'kind': 'decision', 'record_key': later['id']})[0]
    assert not origin['stale']
    assert origin['request_target']['record_key'] == earlier['id']


def test_concurrent_saves_of_one_receipt_have_one_origin(provenance, app, owner, program):
    receipt, _ = make_receipt(provenance, owner, program)
    barrier = Barrier(2)
    results = []
    def save():
        with app.app_context():
            barrier.wait(timeout=10)
            try: results.append(save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt))
            except DomainError as error: results.append(error)
    workers = [Thread(target=save) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=15)
    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 2
    assert sum(isinstance(result, DomainError) for result in results) == 1
    assert next(result for result in results if isinstance(result, DomainError)).code == 'stale_evidence'
    assert get_db().execute('SELECT COUNT(*) FROM proposals').fetchone()[0] == 1
    assert get_db().execute('SELECT COUNT(*) FROM ai_origins').fetchone()[0] == 1


def test_source_writer_is_excluded_between_validation_and_attachment(provenance, monkeypatch, app, owner, program, submitted):
    receipt, _ = make_receipt(provenance, owner, program, kinds=('response',))
    original = provenance._attach_verified
    observed = []
    def attach(*args, **kwargs):
        def contend():
            connection = connect(app.config['DATABASE'])
            try:
                connection.execute('PRAGMA busy_timeout=1')
                connection.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as error: observed.append(str(error))
            finally: connection.close()
        worker = Thread(target=contend)
        worker.start(); worker.join(timeout=5)
        assert not worker.is_alive()
        return original(*args, **kwargs)
    monkeypatch.setattr(provenance, '_attach_verified', attach)
    saved = save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None, ai_receipt=receipt)
    assert observed == ['database is locked']
    assert not provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']})[0]['stale']


def test_unknown_or_quarantined_reference_cannot_issue_receipt(provenance, owner, program, submitted):
    receipt, output = make_receipt(provenance, owner, program, kinds=('response',))
    manifest = {**output['sources'][0], 'record_key': 'unknown'}
    with pytest.raises(DomainError): provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, [manifest], output)
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    with pytest.raises(DomainError): provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, output['sources'], output)


def test_origins_do_not_disclose_quarantined_target_content(provenance, owner, program, submitted):
    response_id = submitted['response_ids'][0]
    receipt, _ = make_receipt(provenance, owner, program, kinds=('response',))
    saved = save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Fictional first perspective', 'response_ids': [response_id]}, None, ai_receipt=receipt)
    redact_response(owner, response_id, 'privacy_request')
    origins = provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']})
    assert origins[0]['stale'] and origins[0]['target_changed']
    assert 'Fictional first perspective' not in json.dumps(origins)


def test_generation_issues_receipt_but_zero_origin_rows(provenance, monkeypatch, app, tmp_path, owner, program):
    from onpf.drafting import service
    key = tmp_path / 'fictional-key'
    key.write_text('fictional-secret', encoding='utf8')
    key.chmod(0o600)
    app.config.update(AI_DRAFTING_ENABLED=True, AI_GATEWAY_KEY_FILE=str(key), AI_GATEWAY_URL='http://127.0.0.1:4000/v1')
    handle = next(source['handle'] for source in evidence.catalog(owner, program['id'], {'kind': 'proposal'}) if source['kind'] == 'program')
    output = {'fields': {'title': 'Option', 'text': 'Unsaved suggestion'}, 'sources': [handle], 'uncertainties': [], 'questions': []}
    monkeypatch.setattr(service.gateway, 'complete', lambda *_: json.dumps(output))
    result = service.generate(owner, program['id'], {'kind': 'proposal'}, [handle], '', {})
    payload = provenance.validate_receipt(owner, program['id'], {'kind': 'proposal'}, result['receipt'])
    assert payload['generated_hash'] == evidence._hash(result['fields'])
    assert payload['output_hash'] == evidence._hash({key: value for key, value in result.items() if key != 'receipt'})
    assert 'Unsaved suggestion' not in json.dumps(payload)
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()
    saved = save_proposal(owner, program['id'], result['fields'], None, ai_receipt=result['receipt'])
    assert provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': saved['id']})


def test_internal_attachment_preserves_signed_kind_and_existing_identity(provenance, owner, program):
    saved = save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Wording'}, None)
    other = save_proposal(owner, program['id'], {'title': 'Other', 'text': 'Other wording'}, None)
    target = {'kind': 'proposal', 'record_key': saved['id']}
    receipt, _ = make_receipt(provenance, owner, program, target=target)
    with transaction():
        verified = provenance._prepare_save(owner, program['id'], 'proposal', receipt, record_key=saved['id'])
        with pytest.raises(DomainError):
            provenance._attach_verified(owner, program['id'], {'kind': 'proposal', 'record_key': other['id']}, verified, other)
        with pytest.raises(DomainError):
            provenance._attach_verified(owner, program['id'], {'kind': 'questions', 'record_key': 'core-1-1'}, verified,
                                        next(q for q in evidence._catalog(owner, program['id']) if q['kind'] == 'question')['content'])
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()


def test_internal_question_attachment_preserves_signed_focus(provenance, owner, program):
    question = save_question(owner, program['id'], {'text': 'Other stage?', 'stage': 2, 'document_key': 'overview', 'answer_type': 'text'}, program['revision'])
    receipt, _ = make_receipt(provenance, owner, program, target={'kind': 'questions', 'stage': 1, 'document_key': 'overview'})
    with transaction():
        verified = provenance._prepare_save(owner, program['id'], 'questions', receipt, stage=1, document_key='overview')
        with pytest.raises(DomainError): provenance._attach_verified(owner, program['id'], {'kind': 'questions', 'record_key': question['id']}, verified, question)
    assert not get_db().execute('SELECT 1 FROM ai_origins').fetchone()
