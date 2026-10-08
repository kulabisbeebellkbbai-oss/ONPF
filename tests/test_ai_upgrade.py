import json
from pathlib import Path

import pytest

from onpf.archives import service as archives
from onpf.archives.validation import digest
from onpf.db import get_db
from onpf.drafting import evidence, provenance
from onpf.errors import DomainError
from onpf.inquiries import clarifications
from onpf.programs.service import get_program


def selected(owner, program, kind, key):
    return evidence.select(owner, program['id'], {'kind': 'questions'}, [f'{kind}:{program["id"]}:{key}'])['sources']


@pytest.mark.parametrize('change', ['correction', 'status', 'removal'])
def test_question_only_evidence_tracks_direct_support_without_aggregate_revision(owner, program, submitted, change):
    from onpf.contributions.service import revise_response
    from onpf.refinement.service import set_disposition, save_proposal
    rid = submitted['response_ids'][0]
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Review support', selected(owner, program, 'response', rid))
    before = selected(owner, program, 'question', 'core-1-1')
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, before, {'fields': {'title': 'Option', 'text': 'Reviewed'}})
    proposal = save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Reviewed'}, None, ai_receipt=receipt)
    original = dict(get_db().execute('SELECT * FROM ai_origins').fetchone())
    revision = get_program(owner, program['id'])['revision']
    if change == 'correction': revise_response(owner, rid, 'Updated response', 'Correction', 1)
    elif change == 'status': set_disposition(owner, rid, 'deferred', 'Later', [])
    else: archives.redact_response(owner, rid, 'privacy_request')
    get_db().execute('UPDATE programs SET revision=? WHERE id=?', (revision, program['id']))
    with pytest.raises(DomainError, match='changed'):
        evidence.assert_current(owner, program['id'], before)
    assert provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': proposal['id']})[0]['stale']
    assert dict(get_db().execute('SELECT * FROM ai_origins').fetchone()) == original


def test_same_handle_refresh_changes_question_identity(owner, program, submitted):
    from onpf.contributions.service import revise_response
    rid = submitted['response_ids'][0]
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Support', selected(owner, program, 'response', rid))
    revise_response(owner, rid, 'New', 'Correction', 1)
    before = selected(owner, program, 'question', 'core-1-1')
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Support', selected(owner, program, 'response', rid))
    assert before[0]['content_hash'] != selected(owner, program, 'question', 'core-1-1')[0]['content_hash']


def test_self_and_cyclic_context_saves_are_finite_and_immediately_issuable(owner, program):
    for key, support in [('core-1-1', 'core-1-1'), ('core-1-2', 'core-1-1'), ('core-1-1', 'core-1-2')]:
        context = clarifications.save_question_context(owner, program['id'], key, 'Support', selected(owner, program, 'question', support))
        evidence.assert_current(owner, program['id'], context['sources'])
        manifest = selected(owner, program, 'question', key)
        evidence.assert_current(owner, program['id'], manifest)
    clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, get_program(owner, program['id'])['revision'])

def rehash(value):
    value['hashes'] = {key: digest(rows) for key, rows in value['tables'].items()}
    value['archive_hash'] = digest({key: item for key, item in value.items() if key != 'archive_hash'})


def old_backup(app, tmp_path):
    """Populate an actual trusted prefix database, never relabel an 018 schema."""
    import sqlite3
    from onpf.db import connect, utcnow
    from onpf.archives.validation import schema, OMITTED
    import onpf
    db = connect(tmp_path / 'old.sqlite3')
    db.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
    prefix = sorted((Path(onpf.__file__).parent / 'migrations').glob('*.sql'))
    prefix = [p for p in prefix if p.name <= '009_contact_log.sql']
    for migration in prefix:
        db.executescript(migration.read_text(encoding='utf-8-sig'))
        db.execute('INSERT INTO schema_migrations VALUES (?,?)', (migration.name, utcnow()))
    db.execute('PRAGMA foreign_keys=OFF')
    for table, metadata in schema(db).items():
        if table == 'schema_migrations': continue
        columns = ','.join('"' + column['name'] + '"' for column in metadata)
        rows = get_db().execute(f'SELECT {columns} FROM "{table}"').fetchall()
        if rows:
            db.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in rows[0])})', [tuple(row) for row in rows])
    tables = {name: [dict(row) for row in db.execute(f'SELECT * FROM "{name}"')] for name in schema(db) if name not in OMITTED}
    for name in ('invitations', 'review_drafts'):
        for row in tables[name]:
            row['token_hash'] = 'disabled:' + row['id']
            row['revoked_at'] = row['revoked_at'] or utcnow()
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_origins'").fetchone()
    assert not db.execute('PRAGMA foreign_key_check').fetchone()
    db.close()
    value = {'classification': 'PRIVATE', 'format_version': 1, 'application_version': '0.1.0',
             'schema_versions': [p.name for p in prefix], 'created_at': utcnow(), 'tables': tables}
    rehash(value)
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value


def test_populated_018_upgrade_and_backup_restore_preserve_frozen_release(app, owner, program, submitted, tmp_path):
    from test_archives import freeze
    from onpf.contributions.service import revise_response
    from onpf.app import create_app
    from onpf.refinement.service import save_proposal
    revise_response(owner, submitted['response_ids'][0], 'Corrected historical view', 'Correction', 1)
    release_id = freeze(owner, program, submitted)
    frozen = dict(get_db().execute('SELECT * FROM releases WHERE id=?', (release_id,)).fetchone())
    path, original = old_backup(app, tmp_path)
    from onpf.db import migrate, connect
    migrate(tmp_path / 'old.sqlite3')
    upgraded = connect(tmp_path / 'old.sqlite3')
    assert dict(upgraded.execute('SELECT * FROM releases').fetchone()) == frozen
    assert upgraded.execute("SELECT 1 FROM schema_migrations WHERE version='018_ai_drafting.sql'").fetchone()
    upgraded.close()
    destination = tmp_path / 'upgraded.sqlite3'
    archives.restore_private(path, destination)
    assert json.loads(path.read_text()) == original
    restored = create_app({'TESTING': True, 'DATABASE': str(destination), 'INSTANCE_PATH': str(tmp_path / 'new-instance')})
    with restored.app_context():
        assert dict(get_db().execute('SELECT * FROM releases').fetchone()) == frozen
        manifests = selected(owner, program, 'response', submitted['response_ids'][0])
        receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, manifests, {'fields': {'title': 'Private AI marker', 'text': 'Reviewed'}})
        save_proposal(owner, program['id'], {'title': 'Private AI marker', 'text': 'Reviewed'}, None, ai_receipt=receipt)
        clarifications.save_question_context(owner, program['id'], 'core-1-1', 'PRIVATE-CONTEXT-MARKER', manifests)
        clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1'], 'reason': 'PRIVATE-ROUND-MARKER'}, get_program(owner, program['id'])['revision'])
        private = tmp_path / 'current.json'
        archives.backup_private(destination, private)
        value = json.loads(private.read_text())
        assert value['application_version'] == '0.3.0'
        archives.restore_private(private, tmp_path / 'roundtrip.sqlite3')
        from onpf.db import connect
        db = connect(tmp_path / 'roundtrip.sqlite3')
        assert dict(db.execute('SELECT * FROM releases').fetchone()) == frozen
        for table in ('ai_origins', 'question_contexts', 'drafting_clarification_rounds', 'clarification_round_questions'):
            assert [dict(r) for r in db.execute(f'SELECT * FROM {table}')] == value['tables'][table]
        db.close()


@pytest.mark.parametrize('damage', ['false_old', 'future', 'missing_hash', 'missing_table', 'corrupt_hash', 'schema_mismatch'])
def test_old_claims_and_hashes_fail_closed(app, owner, program, tmp_path, damage):
    if damage == 'false_old':
        path = tmp_path / 'bad.json'
        archives.backup_private(Path(app.config['DATABASE']), path)
        value = json.loads(path.read_text())
        value['application_version'] = '0.1.0'
    else:
        path, value = old_backup(app, tmp_path)
        if damage == 'future': value['application_version'] = '0.3.0'
        elif damage == 'missing_hash': del value['hashes']['programs']
        elif damage == 'missing_table': del value['tables']['programs']
        elif damage == 'corrupt_hash': value['tables']['programs'][0]['title'] = 'Tampered'
        else: value['schema_versions'].remove('007a_exports.sql')
    if damage not in ('missing_hash', 'corrupt_hash'): rehash(value)
    path.write_text(json.dumps(value))
    with pytest.raises(DomainError): archives.restore_private(path, tmp_path / 'rejected.sqlite3')
    assert not (tmp_path / 'rejected.sqlite3').exists()
    assert not list(tmp_path.glob('.onpf-restore-*'))


@pytest.mark.parametrize('damage', ['target', 'request_target', 'source', 'cross_program', 'revision', 'hash_type', 'source_text', 'context', 'round'])
def test_private_metadata_references_fail_closed(app, owner, program, submitted, tmp_path, damage):
    from onpf.refinement.service import save_proposal
    from onpf.programs.service import create_program
    other = create_program(owner, {'title': 'Other'})
    manifests = selected(owner, program, 'response', submitted['response_ids'][0])
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, manifests, {'fields': {'title': 'Option', 'text': 'Text'}})
    save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Text'}, None, ai_receipt=receipt)
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Reason', manifests)
    clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, get_program(owner, program['id'])['revision'])
    path = tmp_path / 'bad.json'
    archives.backup_private(Path(app.config['DATABASE']), path)
    value = json.loads(path.read_text())
    origin = value['tables']['ai_origins'][0]
    if damage == 'target': origin['target_key'] = '00000000-0000-0000-0000-000000000000'
    elif damage == 'request_target': origin['request_target'] = '{}'
    elif damage == 'context': value['tables']['question_contexts'][0]['question_id'] = 'missing-question'
    elif damage == 'round': value['tables']['drafting_clarification_rounds'][0]['sources'] = '{}'
    else:
        source = json.loads(origin['sources'])
        if damage == 'source': source[0]['record_key'] = '00000000-0000-0000-0000-000000000000'
        elif damage == 'cross_program': origin['program_id'] = other['id']
        elif damage == 'revision': source[0]['revision'] += 20
        elif damage == 'hash_type': source[0]['content_hash'] = 'invalid'
        else: source[0]['content'] = 'PRIVATE unapproved content'
        origin['sources'] = json.dumps(source)
    rehash(value)
    path.write_text(json.dumps(value))
    with pytest.raises(DomainError): archives.restore_private(path, tmp_path / 'rejected.sqlite3')
    assert not (tmp_path / 'rejected.sqlite3').exists()

def test_standalone_clarification_copies_quarantined_and_recoverable(app, owner, other_owner, program, tmp_path):
    from onpf.contributions.service import submit_responses
    from onpf.programs.service import create_program
    from onpf.releases.service import prepare_candidate, approve_candidate, get_release
    from onpf.inquiries import service
    from onpf.app import create_app
    frozen_id = approve_candidate(owner, prepare_candidate(owner, program['id'], program['revision'])['id'])['release_id']
    frozen = get_release(owner, frozen_id)
    copied = 'UNIQUE PRIVATE CONTRIBUTOR COPY'
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Issued ' + copied, [])
    batch = clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1', 'core-1-2'], 'reason': 'Round ' + copied}, get_program(owner, program['id'])['revision'])
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Draft ' + copied, [])
    other = create_program(other_owner, {'title': 'Other'})
    clarifications.save_question_context(other_owner, other['id'], 'core-1-1', 'Other ' + copied, [])
    clean_id = batch['questions'][1]['id']
    clarifications.defer_question(owner, program['id'], batch['id'], clean_id, 'Deferral ' + copied, get_program(owner, program['id'])['revision'])
    clarifications.defer_question(owner, program['id'], batch['id'], clean_id, 'Clean deferral', get_program(owner, program['id'])['revision'])
    before = selected(owner, program, 'issued_question', clean_id)
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, before, {'fields': {'title': 'Clean', 'text': 'Clean'}})
    response = submit_responses(owner, batch['id'], 'standalone', [{'question_id': clean_id, 'text': copied, 'attribution': 'anonymous'}])
    originals = {table: [dict(r) for r in get_db().execute(f'SELECT * FROM {table}')] for table in ('drafting_clarification_rounds', 'clarification_round_questions', 'question_deferrals')}
    report = archives.redact_response(owner, response['response_ids'][0], 'privacy_request')
    expected = {'question_contexts', 'drafting_clarification_rounds', 'clarification_round_questions', 'question_deferrals'}
    assert expected <= {r['table'] for r in report['affected_records']}
    assert report['other_program_record_count'] == 1
    assert all(r['program_id'] == program['id'] for r in report['affected_records'])
    assert get_release(owner, frozen_id) == frozen
    assert get_db().execute('SELECT text FROM responses WHERE id=?', (response['response_ids'][0],)).fetchone()[0] == ''
    for table, records in originals.items(): assert [dict(r) for r in get_db().execute(f'SELECT * FROM {table}')] == records
    available = evidence.catalog(owner, program['id'], {'kind': 'review'})
    assert copied not in json.dumps(available)
    assert not any(s['kind'] == 'batch' and s['record_key'] == batch['id'] for s in available)
    assert not any(s['kind'] == 'question' and s['record_key'] == 'core-1-1' for s in available)
    clean = next(s for s in available if s['kind'] == 'issued_question' and s['record_key'] == clean_id)
    assert [d['reason'] for d in clean['content']['deferrals']] == ['Clean deferral']
    with pytest.raises(DomainError): evidence.assert_current(owner, program['id'], before)
    with pytest.raises(DomainError): provenance.validate_receipt(owner, program['id'], {'kind': 'proposal'}, receipt)
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Repaired', [])
    assert selected(owner, program, 'question', 'core-1-1')
    path = tmp_path / 'removed.json'
    archives.backup_private(Path(app.config['DATABASE']), path)
    archives.restore_private(path, tmp_path / 'removed.sqlite3')
    restored = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'removed.sqlite3'), 'INSTANCE_PATH': str(tmp_path / 'removed-instance')})
    with restored.app_context():
        assert get_release(owner, frozen_id) == frozen
        assert copied not in json.dumps(evidence.catalog(owner, program['id'], {'kind': 'review'}))
        assert clarifications.question_context(owner, program['id'], 'core-1-1')['reason'] == 'Repaired'
        for table, records in originals.items(): assert [dict(r) for r in get_db().execute(f'SELECT * FROM {table}')] == records
        assert get_db().execute('SELECT COUNT(*) FROM quarantined_content').fetchone()[0] == 5


def test_public_package_print_review_intake_exclude_private_ai_metadata(app, login_client, owner, program, tmp_path):
    from onpf.refinement.service import create_review_draft, read_review_draft, save_proposal
    from onpf.inquiries import service
    from onpf.releases.service import prepare_candidate, approve_candidate, get_release
    from test_exports import package, all_text
    credential = 'sk-PRIVATE-CREDENTIAL-MARKER'
    credential_file = tmp_path / 'gateway-key'
    credential_file.write_text(credential)
    app.config['AI_GATEWAY_KEY_FILE'] = str(credential_file)
    manifests = selected(owner, program, 'program', program['id'])
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, manifests, {'fields': {'title': 'Option', 'text': 'Reviewed'}})
    save_proposal(owner, program['id'], {'title': 'Option', 'text': 'Reviewed'}, None, ai_receipt=receipt)
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'PRIVATE-CONTEXT-MARKER', manifests)
    batch = clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1'], 'reason': 'PRIVATE-ROUND-MARKER'}, get_program(owner, program['id'])['revision'])
    clarifications.defer_question(owner, program['id'], batch['id'], batch['questions'][0]['id'], 'PRIVATE-DEFERRAL-MARKER', get_program(owner, program['id'])['revision'])
    record = get_release(owner, approve_candidate(owner, prepare_candidate(owner, program['id'], get_program(owner, program['id'])['revision'], clarifications_reviewed=True)['id'])['release_id'])
    review = create_review_draft(owner, program['id'], ['overview'])
    invite = service.resolve_invitation(service.create_invitation(owner, batch['id']))
    projections = [all_text(package(owner, record, tmp_path)), login_client.get('/exports/releases/' + record['id'] + '/print').text,
                   json.dumps(read_review_draft(review['token'])), json.dumps(service.get_batch(invite, batch['id']))]
    markers = ['PRIVATE-CONTEXT-MARKER', 'PRIVATE-ROUND-MARKER', 'PRIVATE-DEFERRAL-MARKER', credential,
               'ai_origins', 'saved_sources', 'generated_hash', 'context_revision']
    for output in projections:
        assert all(marker not in output for marker in markers)
    private = tmp_path / 'credentials-excluded.json'
    archives.backup_private(Path(app.config['DATABASE']), private)
    assert credential not in private.read_text() and str(credential_file) not in private.read_text()


def test_interrupted_compatibility_restore_cleans_unpublished_database(app, owner, program, tmp_path, monkeypatch):
    from onpf.archives import compatibility
    path, _ = old_backup(app, tmp_path)
    original = compatibility.apply_migrations
    def interrupted(db, names):
        original(db, names)
        raise RuntimeError('Interrupted upgrade')
    monkeypatch.setattr(compatibility, 'apply_migrations', interrupted)
    with pytest.raises(RuntimeError, match='Interrupted'):
        archives.restore_private(path, tmp_path / 'interrupted.sqlite3')
    assert not (tmp_path / 'interrupted.sqlite3').exists()
    assert not list(tmp_path.glob('.onpf-restore-*'))

def test_historical_sources_restore_after_correction_supersession_closure_and_deferral(app, owner, program, submitted, tmp_path):
    from onpf.contributions.service import revise_response
    from onpf.refinement.service import record_decision, save_proposal
    from onpf.inquiries import service
    decision = record_decision(owner, program['id'], {'outcome': 'Old outcome', 'rationale': 'Old rationale'})
    round_ = clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, get_program(owner, program['id'])['revision'])
    catalog = evidence.catalog(owner, program['id'], {'kind': 'proposal'})
    chosen = [s['handle'] for s in catalog if s['kind'] in {'program', 'decision', 'batch', 'issued_question', 'response', 'document'}]
    manifests = evidence.select(owner, program['id'], {'kind': 'proposal'}, chosen)['sources']
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, manifests, {'fields': {'title': 'Historical', 'text': 'Reviewed'}})
    proposal = save_proposal(owner, program['id'], {'title': 'Historical', 'text': 'Reviewed'}, None, ai_receipt=receipt)
    original = dict(get_db().execute('SELECT * FROM ai_origins').fetchone())
    record_decision(owner, program['id'], {'outcome': 'Successor', 'rationale': 'New judgment', 'supersedes_id': decision['id']})
    revise_response(owner, submitted['response_ids'][0], 'New text', 'Correction', 1)
    service.close_batch(owner, round_['id'])
    clarifications.defer_question(owner, program['id'], round_['id'], round_['questions'][0]['id'], 'Later deferral', get_program(owner, program['id'])['revision'])
    path = tmp_path / 'history.json'
    archives.backup_private(Path(app.config['DATABASE']), path)
    archives.restore_private(path, tmp_path / 'history.sqlite3')
    from onpf.app import create_app
    restored = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'history.sqlite3'), 'INSTANCE_PATH': str(tmp_path / 'history-instance')})
    with restored.app_context():
        assert dict(get_db().execute('SELECT * FROM ai_origins').fetchone()) == original
        assert provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': proposal['id']})[0]['stale']


@pytest.mark.parametrize('damage', [None, 'immutable_hash', 'lineage', 'local_scope'])
def test_vanished_adoption_history_validates_surviving_version_witnesses(app, owner, other_owner, program, tmp_path, damage):
    from test_materials import released
    from onpf.materials import service
    from onpf.programs.service import create_program
    from onpf.refinement.service import save_proposal
    source_program = create_program(other_owner, {'title': 'Source program'})
    draft, version = released(other_owner, source_program)
    derivative = service.derive_material(other_owner, source_program['id'], version['id'])
    local_version = service.release_material(other_owner, derivative['id'], derivative['revision'])
    service.adopt_material(owner, program['id'], local_version['id'], get_program(owner, program['id'])['revision'])
    manifests = selected(owner, program, 'material_version', local_version['id'])
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, manifests, {'fields': {'title': 'Historical', 'text': 'Reviewed'}})
    proposal = save_proposal(owner, program['id'], {'title': 'Historical', 'text': 'Reviewed'}, None, ai_receipt=receipt)
    service.remove_adoption(owner, program['id'], derivative['id'], get_program(owner, program['id'])['revision'])
    path = tmp_path / 'adoption.json'
    archives.backup_private(Path(app.config['DATABASE']), path)
    value = json.loads(path.read_text())
    if damage:
        origin = value['tables']['ai_origins'][0]
        manifests = json.loads(origin['sources'])
        if damage == 'immutable_hash': manifests[0]['content_hash'] = '0' * 64
        elif damage == 'lineage': manifests[0]['lineage'][0]['content_hash'] = '0' * 64
        else:
            # Draft materials are locally owned; unlike shared versions they
            # cannot claim the requesting program through a deleted adoption.
            source = next(s for s in evidence.catalog(other_owner, source_program['id'], {'kind': 'review'}) if s['kind'] == 'material')
            manifests = [evidence._manifest(source)]
            manifests[0]['handle'] = f'material:{program["id"]}:{draft["id"]}'
        origin['sources'] = json.dumps(manifests)
        rehash(value)
        path.write_text(json.dumps(value))
        with pytest.raises(DomainError): archives.restore_private(path, tmp_path / 'adoption.sqlite3')
    else:
        archives.restore_private(path, tmp_path / 'adoption.sqlite3')
        from onpf.app import create_app
        restored = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'adoption.sqlite3'), 'INSTANCE_PATH': str(tmp_path / 'adoption-instance')})
        with restored.app_context():
            assert provenance.origins(owner, program['id'], {'kind': 'proposal', 'record_key': proposal['id']})[0]['stale']
            with pytest.raises(DomainError): evidence.assert_current(owner, program['id'], manifests)


def test_self_context_origin_and_issued_history_restore_after_refresh(app, owner, program, tmp_path):
    manifests = selected(owner, program, 'question', 'core-1-1')
    target = {'kind': 'questions', 'record_key': 'core-1-1', 'stage': 1, 'document_key': 'overview'}
    question = {'id': 'core-1-1', 'text': 'Reviewed self question', 'stage': 1, 'document_key': 'overview',
                'answer_type': 'text', 'reason': 'Self support', 'source_handles': [manifests[0]['handle']]}
    receipt = provenance.issue_receipt(owner, program['id'], target, manifests, {'fields': {'questions': [question]}})
    clarifications.save_draft_questions(owner, program['id'], {'questions': [question], 'sources': manifests}, get_program(owner, program['id'])['revision'], ai_receipt=receipt)
    assert not provenance.origins(owner, program['id'], target)[0]['stale']
    round_ = clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, get_program(owner, program['id'])['revision'])
    original = dict(get_db().execute('SELECT * FROM ai_origins').fetchone())
    frozen = dict(get_db().execute('SELECT * FROM clarification_round_questions').fetchone())
    clarifications.save_question_context(owner, program['id'], 'core-1-1', 'Refreshed', selected(owner, program, 'question', 'core-1-1'))
    assert provenance.origins(owner, program['id'], target)[0]['target_changed']
    assert dict(get_db().execute('SELECT * FROM ai_origins').fetchone()) == original
    assert dict(get_db().execute('SELECT * FROM clarification_round_questions').fetchone()) == frozen
    path = tmp_path / 'self.json'
    archives.backup_private(Path(app.config['DATABASE']), path)
    archives.restore_private(path, tmp_path / 'self.sqlite3')

@pytest.mark.parametrize('damage', ['target_hash', 'reviewed_hash'])
def test_immutable_decision_origin_hashes_use_surviving_history(app, owner, program, tmp_path, damage):
    from onpf.refinement.service import record_decision
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'decision'}, [], {'fields': {'outcome': 'Decision', 'rationale': 'Reason'}})
    decision = record_decision(owner, program['id'], {'outcome': 'Decision', 'rationale': 'Reason'}, ai_receipt=receipt)
    record_decision(owner, program['id'], {'outcome': 'Successor', 'rationale': 'Later', 'supersedes_id': decision['id']})
    path = tmp_path / 'decision.json'
    archives.backup_private(Path(app.config['DATABASE']), path)
    archives.restore_private(path, tmp_path / 'valid-decision.sqlite3')
    value = json.loads(path.read_text())
    value['tables']['ai_origins'][0][damage] = '0' * 64
    rehash(value)
    path.write_text(json.dumps(value))
    with pytest.raises(DomainError): archives.restore_private(path, tmp_path / 'bad-decision.sqlite3')

def test_issued_support_hash_between_two_removals_retains_historical_exclusions(app, owner, program, tmp_path):
    from onpf.contributions.service import submit_responses
    from onpf.refinement.service import save_proposal
    round_ = clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1']}, get_program(owner, program['id'])['revision'])
    qid = round_['questions'][0]['id']
    response = submit_responses(owner, round_['id'], 'two-removals', [
        {'question_id': qid, 'text': marker, 'attribution': 'anonymous'} for marker in ('FIRST COPIED INPUT', 'SECOND COPIED INPUT')])
    for marker in ('FIRST COPIED INPUT', 'SECOND COPIED INPUT'):
        clarifications.defer_question(owner, program['id'], round_['id'], qid, marker, get_program(owner, program['id'])['revision'])
    archives.redact_response(owner, response['response_ids'][0], 'privacy_request')
    manifests = selected(owner, program, 'issued_question', qid)
    receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, manifests, {'fields': {'title': 'Clean', 'text': 'Clean'}})
    save_proposal(owner, program['id'], {'title': 'Clean', 'text': 'Clean'}, None, ai_receipt=receipt)
    archives.redact_response(owner, response['response_ids'][1], 'privacy_request')
    path = tmp_path / 'two-removals.json'
    archives.backup_private(Path(app.config['DATABASE']), path)
    archives.restore_private(path, tmp_path / 'two-removals.sqlite3')
