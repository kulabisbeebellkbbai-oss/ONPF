"""Review-only lifecycle evidence: real authenticated HTTP, no persisted witnesses."""
import json
import re
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from onpf.archives import service as archives
from onpf.auth.models import Principal
from onpf.db import get_db
from onpf.drafting import evidence, provenance, service
from onpf.errors import DomainError
from onpf.inquiries import clarifications
from onpf.programs.service import create_program, get_program, save_document
from onpf.releases import service as releases
from test_ai_drafting import snapshot, suggestion
from test_ai_routes import hidden, state
from test_ai_upgrade import rehash
from test_releases import prepare, review, two_owners


@contextmanager
def wire(app, tmp_path, output, during=None, status=200):
    calls, failures = [], []
    key = tmp_path / 'fictional-key'
    key.write_text('fictional-private-key', encoding='utf8')
    key.chmod(0o600)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            if self.path != '/v1/chat/completions' or self.headers.get('Authorization') != 'Bearer fictional-private-key':
                self.send_error(403)
                return
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            try:
                if during:
                    with app.app_context(): during()
            except Exception as error:
                failures.append(error)
            body = json.dumps({'choices': [{'message': {'content': output}, 'finish_reason': 'stop'}]}).encode()
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=http.serve_forever, daemon=True)
    worker.start()
    app.config.update(AI_DRAFTING_ENABLED=True, AI_GATEWAY_KEY_FILE=str(key),
                      AI_GATEWAY_URL=f'http://127.0.0.1:{http.server_port}/v1')
    try:
        yield calls
    finally:
        http.shutdown()
        http.server_close()
        worker.join()
        assert not failures, failures


def lifecycle(owner, program):
    rows = [s for s in evidence.catalog(owner, program['id'], {'kind': 'review'}) if s['kind'] == 'lifecycle']
    assert len(rows) == 1, 'Advisory review needs selectable recorded lifecycle evidence'
    return rows[0]


@pytest.mark.parametrize('status,output,code', [(200, '{"fictional":true}', 0),
    (200, 'PRIVATE-GENERATED-TEXT', 1), (503, 'SECRET-PROVIDER-ERROR', 1)])
def test_actual_documented_acceptance_executes_authenticated_http(app, tmp_path, capsys, status, output, code):
    doc = (Path(__file__).parents[1] / 'docs/ai-drafting-setup.md').read_text(encoding='utf8')
    snippet = re.search(r"sudo -u onpf .*? <<'PY'\n(.*?)\nPY", doc, re.S).group(1)
    with wire(app, tmp_path, output, status=status) as calls:
        # Only operator-specific endpoint and key path change; execute the actual settings and smoke body.
        snippet = snippet.replace("'http://127.0.0.1:4000/v1'", repr(app.config['AI_GATEWAY_URL']))
        snippet = snippet.replace("'/etc/onpf/ai-gateway-key'", repr(app.config['AI_GATEWAY_KEY_FILE']))
        with pytest.raises(SystemExit) as result:
            exec(compile(snippet, 'docs/ai-drafting-setup.md', 'exec'), {})
    assert len(calls) == 1, 'Documented smoke must reach authenticated HTTP'
    assert calls[0]['model'] == 'onpf-drafting' and calls[0]['max_tokens'] == 512
    assert calls[0]['response_format'] == {'type': 'json_object'}
    assert 'FICTIONAL acceptance only' in calls[0]['messages'][0]['content']
    assert result.value.code == code
    assert capsys.readouterr().out == ('fictional_acceptance_passed\n' if code == 0 else 'fictional_acceptance_failed\n')


@pytest.mark.parametrize('phase', ['none', 'pending', 'partial', 'released', 'withdrawn', 'stale', 'released_changed'])
def test_lifecycle_payload_exact_status_private_exclusions_and_zero_writes(app, tmp_path, owner, other_owner, program, submitted, phase):
    two_owners(owner, other_owner, program)
    review(owner, submitted)
    # This copied draft causes real privacy withdrawal, while lifecycle itself contains only status/hashes.
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional first perspective'}}, get_program(owner, program['id'])['revision'])
    candidate = prepare(owner, program) if phase != 'none' else None
    if phase in {'partial', 'released', 'withdrawn', 'released_changed'}: releases.approve_candidate(owner, candidate['id'])
    if phase in {'released', 'withdrawn', 'released_changed'}: releases.approve_candidate(other_owner, candidate['id'])
    if phase == 'withdrawn': archives.redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    if phase in {'stale', 'released_changed'}:
        save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Changed draft'}}, get_program(owner, program['id'])['revision'])
    row = lifecycle(owner, program)
    assert row['default_selected']
    before = snapshot()
    with wire(app, tmp_path, json.dumps(suggestion('review', row['handle']))) as calls:
        result = service.generate(owner, program['id'], {'kind': 'review'}, [row['handle']], '', {})
    assert snapshot() == before
    outgoing = json.loads(calls[0]['messages'][1]['content'])['evidence']['sources']
    assert len(outgoing) == 1 and outgoing[0]['content'] == row['content']
    content = outgoing[0]['content']
    assert content['operating_status'] == 'pending'
    assert content['scope'] == 'Program-wide recorded design lifecycle; design approval does not confirm operating permission.'
    if candidate:
        item = content['candidates'][0]
        assert item['candidate_id'] == candidate['id'] and item['candidate_hash'] == candidate['content_hash']
        assert item['approval_rule'] == 'all' and item['required_approvals'] == 2
        assert item['matching_approvals'] == (2 if phase in {'released', 'withdrawn', 'released_changed'} else 1 if phase == 'partial' else 0)
        assert item['approval_hashes_match'] is True
        assert item['current_draft_matches'] == (phase not in {'stale', 'released_changed'})
        assert item['candidate_stale'] == (phase == 'stale')
        assert item['state'] == ('withdrawn' if phase == 'withdrawn' else 'released' if phase in {'released', 'released_changed'} else 'pending')
        if item['release']:
            assert item['release']['content_hash'] == candidate['content_hash']
            assert item['release']['matches_candidate'] is True
    else:
        assert content['candidates'] == []
    serialized = json.dumps(outgoing)
    for private in [owner.user_id, other_owner.user_id, 'owner_labels', 'prepared_by', 'snapshot', 'Fictional first perspective', 'change_notes', 'token', 'password', 'Fictional alias']:
        assert private not in serialized
    assert 'content' not in result['sources'][0]


@pytest.mark.parametrize('change', ['prepare', 'approval', 'release', 'withdrawal', 'draft', 'demotion'])
def test_selected_lifecycle_rechecked_after_http_and_on_receipt_without_revision(app, tmp_path, owner, other_owner, program, submitted, change):
    two_owners(owner, other_owner, program)
    review(owner, submitted)
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional first perspective'}}, get_program(owner, program['id'])['revision'])
    candidate = prepare(owner, program)
    if change in {'release', 'withdrawal'}: releases.approve_candidate(owner, candidate['id'])
    if change == 'withdrawal': releases.approve_candidate(other_owner, candidate['id'])
    row = lifecycle(owner, program)
    with wire(app, tmp_path, json.dumps(suggestion('review', row['handle']))):
        first = service.generate(owner, program['id'], {'kind': 'review'}, [row['handle']], '', {})
    revision = get_program(owner, program['id'])['revision']
    after_mutation = []
    def mutate():
        if change == 'prepare': prepare(owner, program)
        elif change == 'approval': releases.approve_candidate(owner, candidate['id'])
        elif change == 'release': releases.approve_candidate(other_owner, candidate['id'])
        elif change == 'withdrawal': archives.redact_response(owner, submitted['response_ids'][0], 'privacy_request')
        elif change == 'draft':
            save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Changed concurrently'}}, revision)
        else: get_db().execute("UPDATE memberships SET role='contributor' WHERE program_id=? AND user_id=?", (program['id'], owner.user_id))
        # Prove exact state revalidation independently of the coarse revision counter.
        get_db().execute('UPDATE programs SET revision=? WHERE id=?', (revision, program['id']))
        after_mutation.append(snapshot())
    with wire(app, tmp_path, json.dumps(suggestion('review', row['handle'])), mutate) as calls:
        with pytest.raises(DomainError) as refused:
            service.generate(owner, program['id'], {'kind': 'review'}, [row['handle']], '', {})
    assert refused.value.code == ('forbidden' if change == 'demotion' else 'stale_evidence')
    assert len(calls) == 1 and snapshot() == after_mutation[0]
    with pytest.raises(DomainError) as old:
        provenance.validate_receipt(owner, program['id'], {'kind': 'review'}, first['receipt'])
    assert old.value.code == refused.value.code
    assert snapshot() == after_mutation[0]


def test_lifecycle_selection_authority_and_all_persistence_paths_reject(app, tmp_path, owner, other_owner, facilitator, program):
    row = lifecycle(owner, program)
    manifest = evidence._manifest(row)
    foreign = create_program(other_owner, {'title': 'Foreign lifecycle'})
    foreign_handle = lifecycle(other_owner, foreign)['handle']
    for actor, handle in [(owner, foreign_handle), (other_owner, row['handle']), (Principal(None, 'capability', 'batch'), row['handle'])]:
        with pytest.raises(DomainError): evidence.select(actor, program['id'], {'kind': 'review'}, [handle])
    assert lifecycle(facilitator, program)['content'] == row['content']
    for kind in ['questions', 'proposal', 'decision', 'document']:
        target = {'kind': kind, **({'document_key': 'overview'} if kind == 'document' else {})}
        assert not any(s['kind'] == 'lifecycle' for s in evidence.catalog(owner, program['id'], target))
        with pytest.raises(DomainError): evidence.select(owner, program['id'], target, [row['handle']])
        with pytest.raises(DomainError): provenance.issue_receipt(owner, program['id'], target, [manifest], {'fields': {}})
    before = snapshot()
    with pytest.raises(DomainError): clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Reason', [manifest])
    with pytest.raises(DomainError):
        clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1'], 'reason': 'Reason', 'sources': [manifest]}, program['revision'])
    with pytest.raises(DomainError):
        clarifications.save_draft_questions(owner, program['id'], {'questions': [{'text': 'Question?', 'stage': 1, 'document_key': 'overview', 'source_handles': [row['handle']]}], 'sources': [manifest]}, program['revision'])
    assert snapshot() == before


@pytest.mark.parametrize('action', ['edit', 'followups', 'apply'])
def test_review_actions_revalidate_lifecycle_after_approval_without_revision(app, tmp_path, login_client, owner, other_owner, program, action):
    two_owners(owner, other_owner, program)
    candidate = prepare(owner, program)
    path = f'/programs/{program["id"]}/drafting'
    page = login_client.get(path + '?kind=review')
    handle = lifecycle(owner, program)['handle']
    with wire(app, tmp_path, json.dumps(suggestion('review', handle))):
        result = login_client.post(path, data={'csrf_token': hidden(page.text, 'csrf_token'), 'evidence_state': hidden(page.text, 'evidence_state'), 'target': '{"kind":"review"}',
            'current_fields': '{}', 'handles': handle, 'action': 'generate'})
    assert result.status_code == 200
    revision = get_program(owner, program['id'])['revision']
    releases.approve_candidate(owner, candidate['id'])
    assert get_program(owner, program['id'])['revision'] == revision
    before = snapshot()
    refused = login_client.post(path + ('/apply' if action == 'apply' else ''), data={**state(result.text), 'handles': handle, 'action': action})
    assert refused.status_code == 409
    assert 'Selected evidence changed' in refused.text
    assert snapshot() == before


def test_private_restore_rejects_ephemeral_lifecycle_manifest(app, tmp_path, owner, program):
    manifest = evidence._manifest(lifecycle(owner, program))
    # No new archive kind: even a rehashed archive cannot smuggle this ephemeral source into saved context.
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Reason', [])
    backup = archives.backup_private(Path(app.config['DATABASE']), tmp_path / 'private.json')
    original = json.loads(backup.read_text())
    archives.restore_private(backup, tmp_path / 'valid.sqlite3')
    original['tables']['question_contexts'][0]['sources'] = json.dumps([manifest])
    rehash(original)
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps(original), encoding='utf8')
    with pytest.raises(DomainError): archives.restore_private(bad, tmp_path / 'invalid.sqlite3')
    assert not (tmp_path / 'invalid.sqlite3').exists()


def test_native_program_review_preview_unchecked_exclusion_followup_reset_and_no_apply(app, tmp_path, login_client, owner, program, submitted):
    review(owner, submitted)
    candidate = prepare(owner, program)
    entry = login_client.get('/candidates/' + candidate['id'])
    assert 'program-wide lifecycle' in entry.text
    path = f'/programs/{program["id"]}/drafting'
    page = login_client.get(path + '?kind=review')
    row = lifecycle(owner, program)
    assert 'Recorded design lifecycle' in page.text and candidate['content_hash'] in page.text
    assert 'current draft matches' in page.text
    before = snapshot()
    with wire(app, tmp_path, json.dumps(suggestion('review', row['handle']))) as calls:
        generated = login_client.post(path, data={'csrf_token': hidden(page.text, 'csrf_token'), 'evidence_state': hidden(page.text, 'evidence_state'), 'target': '{"kind":"review"}', 'current_fields': '{}', 'handles': row['handle'], 'action': 'generate'})
    assert generated.status_code == 200
    assert 'Apply selected fields' not in generated.text and 'Save review' not in generated.text
    assert login_client.post(path + '/apply', data=state(generated.text)).status_code == 422
    followup = login_client.post(path, data={**state(generated.text), 'handles': row['handle'], 'action': 'followups'})
    assert followup.status_code == 200
    assert 'Select evidence again' in followup.text
    fields = json.loads(hidden(followup.text, 'current_fields'))
    assert fields['questions'][0]['text'] == 'Who can confirm the unresolved assumption?'
    assert fields['questions'][0]['source_handles'] == []
    assert row['handle'] not in followup.text
    assert not re.search(r'name="handles"[^>]*checked', followup.text)
    assert hidden(followup.text, 'result') == ''
    with pytest.raises(DomainError):
        clarifications.save_draft_questions(owner, program['id'], {'questions': fields['questions'], 'sources': []}, program['revision'], ai_receipt=json.loads(hidden(generated.text, 'result'))['receipt'])
    assert snapshot() == before
    ordinary = f'program:{program["id"]}:{program["id"]}'
    with wire(app, tmp_path, json.dumps(suggestion('review', ordinary))) as excluded:
        service.generate(owner, program['id'], {'kind': 'review'}, [ordinary], '', {})
    assert [s['kind'] for s in json.loads(excluded[0]['messages'][1]['content'])['evidence']['sources']] == ['program']
    assert snapshot() == before
