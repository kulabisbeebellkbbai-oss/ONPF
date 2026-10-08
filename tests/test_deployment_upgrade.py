"""Fictional deployed 0.2 storage upgrades without rewriting frozen evidence."""
import json
from pathlib import Path

import pytest

from onpf.archives import service as archives
from onpf.archives.compatibility import GARDEN_MIGRATIONS
from onpf.archives.validation import OMITTED, digest, schema, validate_database
from onpf.db import apply_migrations, connect, get_db, migrate, utcnow
from onpf.errors import DomainError


def records(db, table):
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]


def assert_retained(db,table,expected):
    actual=records(db,table)
    # Additive columns may supply defaults; every original value, row and
    # ordering remains an exact comparison, including attachment bytes/hashes.
    if expected:
        actual=[{key:row[key] for key in expected[0]} for row in actual]
    assert actual==expected


def rehash(value):
    value['hashes'] = {table: digest(rows) for table, rows in value['tables'].items()}
    value['archive_hash'] = digest({key: item for key, item in value.items() if key != 'archive_hash'})


@pytest.fixture
def deployed(app, owner, program, submitted, tmp_path):
    from onpf.additional_documents.service import save_additional
    from onpf.auth.service import create_session
    from onpf.inquiries.service import issue_batch
    from onpf.permissions.service import save_permission
    from onpf.programs.service import get_program
    from onpf.publications.service import publish
    from onpf.refinement.service import record_decision, save_proposal
    from test_additional_documents import payload
    from test_archives import freeze
    from test_permissions import permission_payload

    # Populate the existing deployment's features before taking an actual prefix
    # schema copy. No AI metadata is relabeled as historical production evidence.
    proposal = save_proposal(owner, program['id'], {'title': 'Original option',
        'text': 'Original frozen proposal', 'theme': 'Access'}, None)
    record_decision(owner, program['id'], {'outcome': 'Retain original',
        'rationale': 'Fictional decision', 'proposal_ids': [proposal['id']]})
    save_proposal(owner, program['id'], {**proposal, 'text': 'Later draft edit'}, proposal['revision'])
    freeze(owner, program, submitted)
    legacy = issue_batch(owner, program['id'], {'title': 'Legacy document clarification',
        'question_ids': ['core-1-1'], 'clarification': {
            'after_step': 'document_review', 'purpose': 'Confirm wording',
            'sources': {'core-1-1': {'source_type': 'document',
                'source_id': 'overview:purpose', 'why': 'Confirm purpose', 'group_label': 'Purpose'}}}})
    save_permission(owner, program['id'], permission_payload(), b'%PDF-1.4 FICTIONAL permission')
    media = save_additional(owner, program['id'], payload(selected=True),
        get_program(owner, program['id'])['revision'], upload=b'%PDF-1.7 fictional poster', filename='poster.pdf')
    publish(owner, program['id'], get_program(owner, program['id'])['revision'],
        reviewed=True, selected_additional_ids=[media['id']])
    create_session(owner)
    get_db().execute('INSERT INTO drafting_attempts VALUES (?,?,?)',
                     ('00000000-0000-0000-0000-000000000001', owner.user_id, 1))

    path = tmp_path / 'deployed.sqlite3'
    db = connect(path)
    db.execute('BEGIN IMMEDIATE')
    apply_migrations(db, GARDEN_MIGRATIONS)
    db.execute('PRAGMA defer_foreign_keys=ON')
    for table, columns in schema(db).items():
        if table == 'schema_migrations':
            continue
        names = [column['name'] for column in columns]
        select = ','.join('"' + name + '"' for name in names)
        source = get_db().execute(f'SELECT {select} FROM "{table}"').fetchall()
        if source:
            db.executemany(f'INSERT INTO "{table}" ({select}) VALUES ({",".join("?" for _ in names)})',
                           [tuple(row) for row in source])
    validate_database(db, include_ai=False)
    db.commit()
    assert 'ai_origins' not in schema(db)
    assert records(db, 'clarification_rounds')[0]['batch_id'] == legacy['id']
    frozen = {table: records(db, table) for table in
              ('release_candidates', 'releases', 'publications', 'decision_proposal_snapshots',
               'permission_records', 'additional_documents', 'clarification_rounds', 'clarification_question_sources')}
    tables = {table: records(db, table) for table in schema(db) if table not in OMITTED}
    now = utcnow()
    for table in ('invitations', 'review_drafts'):
        for row in tables[table]:
            row['token_hash'] = 'disabled:' + row['id']
            row['revoked_at'] = row['revoked_at'] or now
    value = {'classification': 'PRIVATE', 'format_version': 1, 'application_version': '0.2.0',
             'schema_versions': GARDEN_MIGRATIONS, 'created_at': now, 'tables': tables}
    rehash(value)
    backup = tmp_path / 'deployed.json'
    backup.write_text(json.dumps(value), encoding='utf-8')
    db.close()
    return path, backup, value, frozen


def test_populated_production_upgrade_and_original_archive_restore(deployed, tmp_path):
    path, backup, original, frozen = deployed
    migrate(path)
    restored = tmp_path / 'restored-production.sqlite3'
    archives.restore_private(backup, restored)
    assert json.loads(backup.read_text()) == original
    for database in (path, restored):
        db = connect(database)
        try:
            validate_database(db)
            for table, expected in frozen.items():
                assert_retained(db,table,expected)
            assert records(db, 'schema_migrations')[-1]['version'] == '026_document_history_guards.sql'
            for table in ('ai_origins', 'question_contexts', 'drafting_clarification_rounds',
                          'clarification_round_questions', 'question_deferrals'):
                assert records(db, table) == []
            assert db.execute('PRAGMA foreign_key_check').fetchone() is None
        finally:
            db.close()


def test_new_archive_roundtrip_retains_production_hashes_and_omits_attempts(deployed, tmp_path, owner, program):
    from onpf.app import create_app
    from onpf.drafting import evidence, provenance
    from onpf.inquiries import clarifications
    from onpf.programs.service import get_program
    from onpf.refinement.service import save_proposal
    path, _, _, frozen = deployed
    migrate(path)
    upgraded = create_app({'TESTING': True, 'DATABASE': str(path),
                           'INSTANCE_PATH': str(tmp_path / 'upgraded-instance')})
    with upgraded.app_context():
        sources = evidence.select(owner, program['id'], {'kind': 'proposal'},
                                  [f'program:{program["id"]}:{program["id"]}'])['sources']
        receipt = provenance.issue_receipt(owner, program['id'], {'kind': 'proposal'}, sources,
                                           {'fields': {'title': 'New option', 'text': 'Reviewed AI draft'}})
        save_proposal(owner, program['id'], {'title': 'New option', 'text': 'Reviewed AI draft'}, None,
                      ai_receipt=receipt)
        clarifications.save_question_context(owner, program['id'], 'core-1-1', 'New private support', [])
        issued = clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1']},
                                             get_program(owner, program['id'])['revision'])
        clarifications.defer_question(owner, program['id'], issued['id'], issued['questions'][0]['id'],
                                       'Reviewed next cycle', get_program(owner, program['id'])['revision'])
    backup = tmp_path / 'combined.json'
    archives.backup_private(path, backup)
    value = json.loads(backup.read_text())
    assert not OMITTED.intersection(value['tables'])
    restored = tmp_path / 'combined.sqlite3'
    archives.restore_private(backup, restored)
    db = connect(restored)
    try:
        for table, expected in frozen.items():
            assert_retained(db,table,expected)
        for table in ('ai_origins', 'question_contexts', 'drafting_clarification_rounds',
                      'clarification_round_questions', 'question_deferrals'):
            assert records(db, table) == value['tables'][table]
            assert records(db, table)
        assert records(db, 'drafting_attempts') == []
        assert records(db, 'auth_sessions') == []
    finally:
        db.close()


def test_removal_reports_production_and_ai_copies_without_rewriting_frozen_history(
        deployed, tmp_path, owner, program, submitted):
    from onpf.app import create_app
    from onpf.additional_documents.service import save_additional
    from onpf.inquiries import clarifications
    from onpf.programs.service import get_program
    from onpf.publications.service import publish
    from test_additional_documents import payload
    path, _, _, frozen = deployed
    migrate(path)
    upgraded = create_app({'TESTING': True, 'DATABASE': str(path),
                           'INSTANCE_PATH': str(tmp_path / 'removal-instance')})
    with upgraded.app_context():
        marker = get_db().execute('SELECT text FROM responses WHERE id=?',
                                  (submitted['response_ids'][0],)).fetchone()[0]
        media_payload = payload(selected=True)
        media_payload['sections']['ingredients'] = marker
        media = save_additional(owner, program['id'], media_payload,
                                get_program(owner, program['id'])['revision'])
        public = publish(owner, program['id'], get_program(owner, program['id'])['revision'],
                          reviewed=True, selected_additional_ids=[media['id']])
        clarifications.save_question_context(owner, program['id'], 'core-1-1', marker, [])
        clarifications.issue_round(owner, program['id'], {'question_ids': ['core-1-1'], 'reason': marker},
                                    get_program(owner, program['id'])['revision'])
        frozen_ai = records(get_db(), 'drafting_clarification_rounds')
        frozen_public = dict(get_db().execute('SELECT * FROM publications WHERE id=?', (public['id'],)).fetchone())
        report = archives.redact_response(owner, submitted['response_ids'][0], 'privacy_request')
        assert {'additional_documents', 'publications', 'question_contexts', 'drafting_clarification_rounds',
                'clarification_round_questions'} <= {item['table'] for item in report['affected_records']}
        assert records(get_db(), 'drafting_clarification_rounds') == frozen_ai
        assert dict(get_db().execute('SELECT * FROM publications WHERE id=?', (public['id'],)).fetchone()) == frozen_public
        for table in ('release_candidates', 'releases'):
            assert records(get_db(), table) == frozen[table]
        backup = tmp_path / 'removed-combined.json'
        archives.backup_private(path, backup)
        archives.restore_private(backup, tmp_path / 'removed-combined.sqlite3')


@pytest.mark.parametrize('damage', ['round_source', 'proposal_snapshot', 'upload', 'publication',
                                    'hash', 'missing_table', 'false_schema'])
def test_production_archive_validation_precedes_upgrade(deployed, tmp_path, monkeypatch, damage):
    _, backup, value, _ = deployed
    if damage == 'round_source':
        value['tables']['clarification_question_sources'][0]['source_id'] = 'overview:missing'
    elif damage == 'proposal_snapshot':
        value['tables']['decision_proposal_snapshots'] = []
    elif damage == 'upload':
        value['tables']['additional_documents'][0]['upload_sha256'] = '0' * 64
    elif damage == 'publication':
        value['tables']['publications'][0]['source_hash'] = '0' * 64
    elif damage == 'hash':
        value['tables']['programs'][0]['title'] = 'Corrupted'
    elif damage == 'missing_table':
        del value['tables']['additional_documents']
    else:
        value['schema_versions'] = GARDEN_MIGRATIONS + ['018_ai_drafting.sql']
    if damage != 'hash':
        rehash(value)
    backup.write_text(json.dumps(value), encoding='utf-8')
    from onpf.archives import compatibility
    called = []
    apply = compatibility.apply_migrations
    def guarded(db, names):
        called.append(names)
        return apply(db, names)
    monkeypatch.setattr(compatibility, 'apply_migrations', guarded)
    destination = tmp_path / 'rejected.sqlite3'
    with pytest.raises(DomainError):
        archives.restore_private(backup, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob('.onpf-restore-*'))
    if damage != 'false_schema':
        assert called == [GARDEN_MIGRATIONS]
