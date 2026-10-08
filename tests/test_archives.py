"""Fictional private recovery and removal contracts; no real participant input."""
import json
from pathlib import Path

import pytest

from onpf.db import connect, get_db
from onpf.errors import DomainError


def archives():
    from onpf.archives import service
    return service


def freeze(owner, program, submitted):
    from onpf.refinement.service import set_disposition, record_decision
    from onpf.releases.service import prepare_candidate, approve_candidate
    from onpf.programs.service import get_program
    for response in submitted['response_ids']:
        set_disposition(owner, response, 'deferred', 'Fictional next cycle.', [])
    record_decision(owner, program['id'], {'outcome': 'Fictional draft retained', 'rationale': 'Fictional owner judgment', 'proposal_ids': [], 'response_ids': submitted['response_ids']})
    candidate = prepare_candidate(owner, program['id'], get_program(owner, program['id'])['revision'])
    return approve_candidate(owner, candidate['id'])['release_id']


def backup(app, tmp_path):
    path = tmp_path / 'PRIVATE.json'
    archives().backup_private(Path(app.config['DATABASE']), path)
    return path


def rehash(data):
    from onpf.archives.validation import digest
    data['hashes'] = {table: digest(rows) for table, rows in data['tables'].items()}
    data['archive_hash'] = digest({key: value for key, value in data.items() if key != 'archive_hash'})


def test_roundtrip_preserves_history_and_release_hash(app, owner, program, submitted, tmp_path):
    from onpf.contributions.service import revise_response, get_response
    from onpf.releases.service import get_release
    from onpf.auth.service import authenticate
    from onpf.app import create_app
    revise_response(owner, submitted['response_ids'][0], 'Fictional correction', 'Fictional requested correction', 1)
    release_id = freeze(owner, program, submitted)
    history = get_response(owner, submitted['response_ids'][0])['history']
    release = get_release(owner, release_id)
    destination = tmp_path / 'restored.sqlite3'
    archives().restore_private(backup(app, tmp_path), destination)
    restored = create_app({'TESTING': True, 'DATABASE': str(destination), 'INSTANCE_PATH': str(tmp_path / 'restored-instance')})
    with restored.app_context():
        assert authenticate('owner', 'fictional-owner-password') == owner
        assert get_response(owner, submitted['response_ids'][0])['history'] == history
        assert get_release(owner, release_id) == release
        assert get_db().execute('SELECT COUNT(*) FROM decisions').fetchone()[0] == 1


def test_document_sourced_clarification_backup_restores(app, owner, program, tmp_path):
    from onpf.app import create_app
    from onpf.inquiries.service import get_batch, issue_batch

    issued = issue_batch(owner, program['id'], {'title': 'Fictional document follow-up',
        'question_ids': ['core-1-1'], 'clarification': {
            'after_step': 'document_review', 'purpose': 'Confirm the purpose wording',
            'sources': {'core-1-1': {'source_type': 'document', 'source_id': 'overview:purpose',
                'why': 'The wording needs confirmation.', 'group_label': 'Purpose'}}}})
    destination = tmp_path / 'restored.sqlite3'
    archives().restore_private(backup(app, tmp_path), destination)
    restored = create_app({'TESTING': True, 'DATABASE': str(destination),
                           'INSTANCE_PATH': str(tmp_path / 'restored-instance')})
    with restored.app_context():
        assert get_batch(owner, issued['id'])['questions'][0]['clarification_source']['source_id'] == 'overview:purpose'


def test_decision_proposal_snapshot_survives_backup(app, owner, program, tmp_path):
    from onpf.app import create_app
    from onpf.programs.service import get_program
    from onpf.refinement.service import list_decisions, record_decision, save_proposal
    from onpf.releases.service import get_candidate, prepare_candidate

    proposal = save_proposal(owner, program['id'], {'title': 'Original option',
        'theme': 'Access', 'text': 'Original wording', 'response_ids': []}, None)
    record_decision(owner, program['id'], {'outcome': 'Choose original',
        'rationale': 'Fictional reason', 'proposal_ids': [proposal['id']]})
    save_proposal(owner, program['id'], {**proposal, 'title': 'Changed option',
        'text': 'Changed wording'}, proposal['revision'])
    candidate = prepare_candidate(owner, program['id'], get_program(owner, program['id'])['revision'])
    destination = tmp_path / 'restored.sqlite3'
    archives().restore_private(backup(app, tmp_path), destination)
    restored = create_app({'TESTING': True, 'DATABASE': str(destination),
                           'INSTANCE_PATH': str(tmp_path / 'restored-instance')})
    with restored.app_context():
        frozen = list_decisions(owner, program['id'])[0]['proposal_snapshots'][0]
        assert frozen['title'] == 'Original option'
        assert frozen['text'] == 'Original wording'
        assert get_candidate(owner, candidate['id'])['decisions'][0]['proposal_snapshots'][0] == frozen


@pytest.mark.parametrize('damage', ['malformed', 'future', 'missing_reference', 'hash', 'extra_table', 'snapshot'])
def test_invalid_backup_has_no_partial_restore(app, owner, program, submitted, tmp_path, damage):
    freeze(owner, program, submitted)
    path = backup(app, tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if damage == 'future':
        data['format_version'] = 999
    elif damage == 'missing_reference':
        data['tables']['users'] = []
        rehash(data)
    elif damage == 'hash':
        data['tables']['responses'][0]['text'] = 'Fictional tampered'
    elif damage == 'extra_table':
        data['tables']['attacker'] = []
        rehash(data)
    elif damage == 'snapshot':
        data['tables']['release_candidates'][0]['snapshot'] = '{}'
        rehash(data)
    path.write_text('{' if damage == 'malformed' else json.dumps(data), encoding='utf-8')
    destination = tmp_path / 'rejected.sqlite3'
    with pytest.raises(DomainError):
        archives().restore_private(path, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob('.onpf-restore-*'))


def test_existing_destination_refused(app, owner, program, tmp_path):
    path = backup(app, tmp_path)
    destination = Path(app.config['DATABASE'])
    before = destination.read_bytes()
    with pytest.raises(DomainError):
        archives().restore_private(path, destination)
    assert destination.read_bytes() == before


def test_backup_omits_live_credentials(app, owner, program, batch, tmp_path):
    from onpf.auth.service import create_session, token_hash
    from onpf.inquiries.service import create_invitation
    from onpf.refinement.service import create_review_draft
    session = create_session(owner)
    invite = create_invitation(owner, batch['id'])
    review = create_review_draft(owner, program['id'], ['overview'])
    path = backup(app, tmp_path)
    text = path.read_text(encoding='utf-8')
    assert token_hash(session) not in text
    assert token_hash(invite) not in text
    assert review['token'] not in text
    data = json.loads(text)
    assert data['classification'] == 'PRIVATE'
    assert 'auth_sessions' not in data['tables']
    assert 'password_hash' in data['tables']['users'][0]
    target = tmp_path / 'restored.sqlite3'
    archives().restore_private(path, target)
    db = connect(target)
    try:
        assert db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] == 0
        assert db.execute('SELECT revoked_at FROM invitations').fetchone()[0]
        assert db.execute('SELECT revoked_at FROM review_drafts').fetchone()[0]
    finally:
        db.close()


def test_redaction_removes_original_and_revisions(owner, submitted):
    from onpf.contributions.service import revise_response, get_response
    response_id = submitted['response_ids'][1]
    revise_response(owner, response_id, 'Fictional changed private input', 'Fictional private correction reason', 1)
    before = get_response(owner, response_id)
    result = archives().redact_response(owner, response_id, 'privacy_request')
    after = get_response(owner, response_id)
    assert result['response_id'] == response_id
    assert [row['id'] for row in after['history']] == [row['id'] for row in before['history']]
    assert after['original_text'] == after['text'] == ''
    assert after['display_name'] is None
    assert all(row['text'] == row['reason'] == '' and row['entered_by'] is None for row in after['history'])
    assert 'Fictional alias' not in json.dumps(after)
    assert after['redaction']['reason'] == 'privacy_request'
    with pytest.raises(DomainError):
        revise_response(owner, response_id, 'Fictional recapture', 'Fictional correction', after['revision'])


def test_redaction_owner_only_and_nonpersonal_reason(owner, facilitator, other_owner, submitted):
    response_id = submitted['response_ids'][0]
    for actor in (facilitator, other_owner):
        with pytest.raises(DomainError) as denied:
            archives().redact_response(actor, response_id, 'privacy_request')
        assert denied.value.status == 403
    with pytest.raises(DomainError):
        archives().redact_response(owner, response_id, 'Fictional first perspective')


def test_redaction_reports_copies_and_withdraws_without_rewriting(app, owner, program, submitted, tmp_path):
    from onpf.programs.service import save_document, get_program
    from onpf.releases.service import get_release
    from onpf.exports.package import build_package
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional first perspective'}}, get_program(owner, program['id'])['revision'])
    release_id = freeze(owner, program, submitted)
    original = get_db().execute('SELECT snapshot,content_hash,approvals FROM releases WHERE id=?', (release_id,)).fetchone()
    app.config['EXPORT_DIRECTORY'] = str(tmp_path / 'exports')
    package = build_package(owner, release_id, Path(app.config['EXPORT_DIRECTORY']))
    result = archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert result['affected_records']
    assert release_id in result['withdrawn_release_ids']
    assert not package.exists()
    assert get_release(owner, release_id)['state'] == 'withdrawn'
    assert tuple(get_db().execute('SELECT snapshot,content_hash,approvals FROM releases WHERE id=?', (release_id,)).fetchone()) == tuple(original)
    with pytest.raises(DomainError):
        build_package(owner, release_id, package.parent)


def test_id_only_release_scope_stays_available(owner, program, submitted):
    from onpf.releases.service import get_release
    release_id = freeze(owner, program, submitted)
    result = archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert result['withdrawn_release_ids'] == []
    assert get_release(owner, release_id)['state'] == 'released'


def test_historical_owner_and_revision_changes_restore(app, owner, other_owner, program, submitted, tmp_path):
    from onpf.programs.service import update_program, get_program
    from onpf.contributions.service import revise_response
    freeze(owner, program, submitted)
    update_program(owner, program['id'], {'owner_ids': [owner.user_id, other_owner.user_id]}, get_program(owner, program['id'])['revision'])
    revise_response(owner, submitted['response_ids'][0], 'Fictional later correction', 'Fictional later reason', 1)
    destination = tmp_path / 'historical.sqlite3'
    archives().restore_private(backup(app, tmp_path), destination)
    assert destination.exists()


def test_restore_public_import_and_inherited_terms(app, owner, program, submitted, tmp_path):
    from onpf.materials.service import save_material, release_material, adopt_material, get_material
    from onpf.programs.service import get_program
    from onpf.exports.package import build_package
    from onpf.exports.importer import import_program
    from onpf.app import create_app
    material = save_material(owner, program['id'], {'title': 'Fictional licensed material', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional shared content'}, 'ownership_basis': 'third_party', 'permission_basis': 'Fictional permission', 'license': 'Fictional License', 'notices': 'Fictional source notice'}, None)
    version = release_material(owner, material['id'], material['revision'])
    adopt_material(owner, program['id'], version['id'], get_program(owner, program['id'])['revision'])
    release_id = freeze(owner, program, submitted)
    imported = import_program(owner, build_package(owner, release_id, tmp_path / 'public'), 'Fictional imported draft')
    imported_material = dict(get_db().execute('SELECT * FROM materials WHERE program_id=?', (imported['id'],)).fetchone())
    restored_path = tmp_path / 'imported.sqlite3'
    archives().restore_private(backup(app, tmp_path), restored_path)
    restored = create_app({'TESTING': True, 'DATABASE': str(restored_path), 'INSTANCE_PATH': str(tmp_path / 'import-instance')})
    with restored.app_context():
        assert get_material(owner, imported['id'], imported_material['id'])['source_terms'][0]['notices'] == 'Fictional source notice'


def test_affected_shared_version_blocks_later_release(owner, program, submitted):
    from onpf.materials.service import save_material, release_material, adopt_material
    from onpf.programs.service import get_program
    from onpf.releases.service import prepare_candidate
    material = save_material(owner, program['id'], {'title': 'Fictional copied material', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional first perspective'}, 'ownership_basis': 'own_work', 'permission_basis': 'Fictional permission', 'license': 'MIT', 'notices': ''}, None)
    version = release_material(owner, material['id'], material['revision'])
    result = archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert version['id'] in result['affected_material_version_ids']
    with pytest.raises(DomainError):
        adopt_material(owner, program['id'], version['id'], get_program(owner, program['id'])['revision'])


def test_restore_publish_race_preserves_other_file(app, owner, program, tmp_path, monkeypatch):
    import os
    archive = backup(app, tmp_path)
    destination = tmp_path / 'race.sqlite3'
    real_link = os.link
    def racing_link(source, target):
        Path(target).write_bytes(b'fictional concurrent destination')
        return real_link(source, target)
    monkeypatch.setattr(os, 'link', racing_link)
    with pytest.raises(DomainError):
        archives().restore_private(archive, destination)
    assert destination.read_bytes() == b'fictional concurrent destination'
    assert not list(tmp_path.glob('.onpf-restore-*'))


def test_cli_private_operations(tmp_path, capsys):
    from onpf.cli import main
    instance = tmp_path / 'cli-instance'
    assert main(['--instance', str(instance), 'init']) == 0
    private = tmp_path / 'CLI-PRIVATE.json'
    assert main(['--instance', str(instance), 'backup', str(private)]) == 0
    target = tmp_path / 'cli-restored.sqlite3'
    assert main(['restore', str(private), str(target)]) == 0
    assert target.exists()


@pytest.mark.parametrize('damage', ['identity', 'timestamp', 'decision_link', 'frozen_terms', 'document_support'])
def test_semantically_forged_backup_refused(app, owner, program, submitted, tmp_path, damage):
    from onpf.archives.validation import digest
    from onpf.materials.service import save_material, release_material, adopt_material
    from onpf.programs.service import get_program
    material = save_material(owner, program['id'], {'title': 'Fictional terms', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional material'}, 'ownership_basis': 'third_party', 'permission_basis': 'Fictional permission', 'license': 'Fictional license', 'notices': 'Fictional notice'}, None)
    source = release_material(owner, material['id'], material['revision'])
    from onpf.materials.service import derive_material
    derivative = derive_material(owner, program['id'], source['id'])
    version = release_material(owner, derivative['id'], derivative['revision'])
    adopt_material(owner, program['id'], version['id'], get_program(owner, program['id'])['revision'])
    freeze(owner, program, submitted)
    path = backup(app, tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if damage == 'identity':
        data['tables']['proposals'].append({'id': 'not-a-uuid', 'program_id': program['id'], 'title': 'Fictional invalid', 'text': '', 'theme': '', 'revision': 1, 'created_by': owner.user_id, 'created_at': '2026-09-26T00:00:00+00:00', 'updated_at': '2026-09-26T00:00:00+00:00'})
    elif damage == 'timestamp':
        data['tables']['responses'][0]['entered_at'] = 'yesterday'
    else:
        candidate = data['tables']['release_candidates'][0]
        snap = json.loads(candidate['snapshot'])
        if damage == 'document_support':
            snap['document_decision_ids']['overview'] = ['00000000-0000-0000-0000-000000000001']
        elif damage == 'decision_link':
            snap['decisions'][0]['response_links'] = []
        else:
            snap['materials'][0]['source_terms'][0]['license'] = 'Fictional forged permission'
        candidate['snapshot'] = json.dumps(snap, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        candidate['content_hash'] = digest(snap)
        for approval in data['tables']['candidate_approvals']:
            approval['content_hash'] = candidate['content_hash']
        release = data['tables']['releases'][0]
        release['snapshot'] = candidate['snapshot']
        release['content_hash'] = candidate['content_hash']
        stored = json.loads(release['approvals'])
        for approval in stored:
            approval['content_hash'] = candidate['content_hash']
        release['approvals'] = json.dumps(stored)
    rehash(data)
    path.write_text(json.dumps(data), encoding='utf-8')
    target = tmp_path / 'semantic-reject.sqlite3'
    with pytest.raises(DomainError):
        archives().restore_private(path, target)
    assert not target.exists()


def test_removal_report_hides_other_agency_records(owner, other_owner, program, submitted):
    from onpf.programs.service import create_program, save_document, get_program
    other = create_program(other_owner, {'title': 'Fictional separate agency'})
    save_document(other_owner, other['id'], 'overview', {'sections': {'purpose': 'Fictional first perspective'}}, get_program(other_owner, other['id'])['revision'])
    result = archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert result['other_program_record_count'] == 1
    assert other['id'] not in json.dumps(result)
    assert other['id'] in get_db().execute('SELECT report FROM redaction_events').fetchone()[0]


def test_backup_observes_one_committed_snapshot(app, owner, program, tmp_path):
    writer = connect(Path(app.config['DATABASE']))
    writer.execute('BEGIN IMMEDIATE')
    try:
        writer.execute('UPDATE programs SET title=? WHERE id=?', ('Fictional uncommitted title', program['id']))
        archive = json.loads(backup(app, tmp_path).read_text(encoding='utf-8'))
        assert archive['tables']['programs'][0]['title'] == 'Fictional test program'
    finally:
        writer.rollback()
        writer.close()


def test_redacted_history_and_withdrawal_restore(app, owner, program, submitted, tmp_path):
    from onpf.programs.service import save_document, get_program
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional first perspective'}}, get_program(owner, program['id'])['revision'])
    freeze(owner, program, submitted)
    archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    target = tmp_path / 'removed.sqlite3'
    archives().restore_private(backup(app, tmp_path), target)
    db = connect(target)
    try:
        assert db.execute('SELECT COUNT(*) FROM redaction_events').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM withdrawn_releases').fetchone()[0] == 1
        assert db.execute('SELECT text FROM responses WHERE id=?', (submitted['response_ids'][0],)).fetchone()[0] == ''
    finally:
        db.close()


def test_export_and_removal_serialized(app, owner, program, submitted, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from onpf.programs.service import save_document, get_program
    from onpf.exports import package
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional first perspective'}}, get_program(owner, program['id'])['revision'])
    release_id = freeze(owner, program, submitted)
    app.config['EXPORT_DIRECTORY'] = str(tmp_path / 'concurrent-exports')
    rendering, continue_export, removal_started = Event(), Event(), Event()
    real_render = package.render_odt
    def paused_render(document):
        rendering.set()
        assert continue_export.wait(10)
        return real_render(document)
    monkeypatch.setattr(package, 'render_odt', paused_render)
    def export():
        with app.app_context():
            return package.build_package(owner, release_id, Path(app.config['EXPORT_DIRECTORY']))
    def remove():
        with app.app_context():
            removal_started.set()
            return archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    with ThreadPoolExecutor(max_workers=2) as pool:
        exporting = pool.submit(export)
        assert rendering.wait(10)
        removing = pool.submit(remove)
        assert removal_started.wait(10)
        assert not removing.done()
        continue_export.set()
        output = exporting.result(timeout=15)
        assert release_id in removing.result(timeout=15)['withdrawn_release_ids']
        assert not output.exists()
    with pytest.raises(DomainError):
        package.build_package(owner, release_id, output.parent)


def test_unrelated_material_edit_does_not_clear_copy_block(owner, program, submitted):
    from onpf.materials.service import save_material, release_material, get_material
    payload = {'title': 'Fictional copied material', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional first perspective'}, 'ownership_basis': 'own_work', 'permission_basis': 'Fictional permission', 'license': 'MIT', 'notices': ''}
    material = save_material(owner, program['id'], payload, None)
    archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    material = save_material(owner, program['id'], {**payload, 'id': material['id'], 'title': 'Fictional changed title'}, material['revision'])
    with pytest.raises(DomainError):
        release_material(owner, material['id'], material['revision'])


def test_malformed_removal_reason_is_domain_error(owner, submitted):
    with pytest.raises(DomainError) as error:
        archives().redact_response(owner, submitted['response_ids'][0], [])
    assert error.value.status == 422


def test_invalid_mutable_document_refused(app, owner, program, tmp_path):
    path = backup(app, tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    data['tables']['program_documents'][0]['content'] = '{broken'
    rehash(data)
    path.write_text(json.dumps(data), encoding='utf-8')
    destination = tmp_path / 'bad-document.sqlite3'
    with pytest.raises(DomainError):
        archives().restore_private(path, destination)
    assert not destination.exists()


def test_shared_copy_withdraws_downstream_release(app, owner, other_owner, program, submitted, tmp_path):
    from onpf.materials.service import save_material, release_material, adopt_material
    from onpf.programs.service import create_program, get_program
    from onpf.releases.service import prepare_candidate, approve_candidate, get_release
    from onpf.exports.package import build_package
    payload = {'title': 'Fictional shared copy', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional first perspective'}, 'ownership_basis': 'own_work', 'permission_basis': 'Fictional permission', 'license': 'MIT', 'notices': ''}
    material = save_material(owner, program['id'], payload, None)
    version = release_material(owner, material['id'], material['revision'])
    downstream = create_program(other_owner, {'title': 'Fictional downstream agency'})
    adopt_material(other_owner, downstream['id'], version['id'], get_program(other_owner, downstream['id'])['revision'])
    candidate = prepare_candidate(other_owner, downstream['id'], get_program(other_owner, downstream['id'])['revision'])
    release = approve_candidate(other_owner, candidate['id'])['release_id']
    result = archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert release not in result['withdrawn_release_ids']
    assert downstream['id'] not in json.dumps(result)
    assert get_release(other_owner, release)['state'] == 'withdrawn'
    with pytest.raises(DomainError):
        build_package(other_owner, release, tmp_path / 'downstream')
    with pytest.raises(DomainError):
        prepare_candidate(other_owner, downstream['id'], get_program(other_owner, downstream['id'])['revision'])


def test_copied_pending_candidate_in_other_program_cannot_be_approved(owner, other_owner, program, submitted):
    from onpf.programs.service import create_program, get_program
    from onpf.releases.service import prepare_candidate, approve_candidate
    other = create_program(other_owner, {'title': 'Fictional other agency'})
    candidate = prepare_candidate(other_owner, other['id'], get_program(other_owner, other['id'])['revision'], change_notes='Fictional first perspective')
    archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    with pytest.raises(DomainError):
        approve_candidate(other_owner, candidate['id'])


@pytest.mark.parametrize('support_action', ['replace', 'remove'])
def test_clean_supersession_allows_fresh_release_preserving_history(app, owner, program, submitted, tmp_path, support_action):
    from onpf.app import create_app
    from onpf.programs.service import get_program, save_document
    from onpf.refinement.service import record_decision, set_disposition, list_decisions
    from onpf.releases.service import prepare_candidate, approve_candidate, get_release
    from onpf.exports.package import build_package
    copied = record_decision(owner, program['id'], {'outcome': 'Fictional first perspective', 'rationale': 'Fictional owner copied input for this decision.', 'response_ids': [submitted['response_ids'][0]]})
    clean_history = record_decision(owner, program['id'], {'outcome': 'Fictional separate clean decision', 'rationale': 'Fictional independent evidence.'})
    document = {'sections': {'purpose': 'Fictional clean purpose'}, 'decision_ids': [copied['id']]}
    save_document(owner, program['id'], 'overview', document, get_program(owner, program['id'])['revision'])
    old_release_id = freeze(owner, program, submitted)
    old = get_release(owner, old_release_id)
    archives().redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    for response_id in submitted['response_ids']:
        set_disposition(owner, response_id, 'deferred', 'Fictional input removal reviewed; IDs retained.', [])
    with pytest.raises(DomainError) as unsuperseded:
        prepare_candidate(owner, program['id'], get_program(owner, program['id'])['revision'])
    assert unsuperseded.value.code == 'quarantined_content'
    replacement = record_decision(owner, program['id'], {'outcome': 'Fictional independently reviewed replacement', 'rationale': 'Fictional clean evidence supersedes the earlier recorded choice.', 'supersedes_id': copied['id']})
    with pytest.raises(DomainError) as stale_support:
        prepare_candidate(owner, program['id'], get_program(owner, program['id'])['revision'])
    assert stale_support.value.code == 'retired_decision_support'
    assert get_db().execute('SELECT COUNT(*) FROM release_candidates').fetchone()[0] == 1
    support = [replacement['id']] if support_action == 'replace' else []
    save_document(owner, program['id'], 'overview', {**document, 'decision_ids': support}, get_program(owner, program['id'])['revision'])
    candidate = prepare_candidate(owner, program['id'], get_program(owner, program['id'])['revision'])
    frozen_ids = {decision['id'] for decision in candidate['decisions']}
    assert copied['id'] not in frozen_ids
    assert {replacement['id'], clean_history['id']} <= frozen_ids
    assert candidate['document_decision_ids'].get('overview', []) == support
    assert 'Fictional first perspective' not in json.dumps(candidate)
    new_release_id = approve_candidate(owner, candidate['id'])['release_id']
    assert build_package(owner, new_release_id, tmp_path / 'fresh-public').exists()
    withdrawn = get_release(owner, old_release_id)
    assert withdrawn['state'] == 'withdrawn'
    assert {key: withdrawn[key] for key in ('content_hash', 'approvals', 'decisions', 'documents', 'document_decision_ids')} == {key: old[key] for key in ('content_hash', 'approvals', 'decisions', 'documents', 'document_decision_ids')}
    assert copied in list_decisions(owner, program['id'])
    target = tmp_path / 'superseded-restore.sqlite3'
    archives().restore_private(backup(app, tmp_path), target)
    restored = create_app({'TESTING': True, 'DATABASE': str(target), 'INSTANCE_PATH': str(tmp_path / 'superseded-instance')})
    with restored.app_context():
        assert copied in list_decisions(owner, program['id'])
        assert get_release(owner, old_release_id)['state'] == 'withdrawn'
        assert get_release(owner, old_release_id)['content_hash'] == old['content_hash']
        assert get_release(owner, new_release_id)['decisions'] == candidate['decisions']
        assert build_package(owner, new_release_id, tmp_path / 'restored-public').exists()
