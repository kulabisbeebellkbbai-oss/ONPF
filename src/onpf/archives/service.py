"""Local-admin private recovery, owner-authorized removal, and content quarantine."""
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

from flask import current_app
from onpf.auth.models import Principal
from onpf.auth.service import require_role
from onpf.db import Record, connect, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.archives.validation import APPLICATION_VERSION, FORMAT_VERSION, OMITTED, digest, encode, invalid, load, schema, schema_versions

REASONS = {'privacy_request': 'Participant privacy request', 'retention_expired': 'Retention period expired',
           'captured_in_error': 'Captured in error', 'legal_requirement': 'Required removal under local policy or law'}


def _publish(temporary, destination):
    """A same-directory hard link atomically publishes without replacing a raced file."""
    try:
        os.link(temporary, destination)
    except FileExistsError as error:
        raise DomainError('destination_exists', 'The destination already exists; choose a new path.', 409) from error


def backup_private(database_path: Path, destination: Path) -> Path:
    database_path, destination = Path(database_path), Path(destination)
    if not database_path.is_file():
        raise DomainError('missing_database', 'Choose an existing local database.', 404)
    if destination.exists():
        raise DomainError('destination_exists', 'The backup destination already exists; choose a new path.', 409)
    # SQLite's online backup API obtains one coherent committed snapshot, including WAL.
    source = sqlite3.connect(database_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    snapshot = sqlite3.connect(':memory:')
    snapshot.row_factory = sqlite3.Row
    try:
        source.backup(snapshot)
        if sorted(row[0] for row in snapshot.execute('SELECT version FROM schema_migrations')) != schema_versions():
            invalid()
        tables = {}
        now = utcnow()
        for table in schema(snapshot):
            if table in OMITTED:
                continue
            rows = [dict(row) for row in snapshot.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            if table in ('invitations', 'review_drafts'):
                for row in rows:
                    row['token_hash'] = 'disabled:' + row['id']
                    row['revoked_at'] = row['revoked_at'] or now
            tables[table] = rows
        value = {'classification': 'PRIVATE', 'format_version': FORMAT_VERSION, 'application_version': APPLICATION_VERSION,
                 'schema_versions': schema_versions(), 'created_at': now, 'tables': tables,
                 'hashes': {table: digest(rows) for table, rows in tables.items()}}
        value['archive_hash'] = digest(value)
    finally:
        source.close()
        snapshot.close()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.onpf-backup-', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(encode(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        _publish(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def restore_private(archive_path: Path, new_database_path: Path) -> None:
    destination = Path(new_database_path)
    if destination.exists():
        raise DomainError('destination_exists', 'Restore requires a new nonexistent database path. Existing storage is unchanged.', 409)
    value = load(archive_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.onpf-restore-', suffix='.sqlite3', dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    db = None
    try:
        from onpf.archives.compatibility import restore_supported_envelope
        db = connect(temporary)
        restore_supported_envelope(value, db)
        db.close()
        db = None
        # Flush the complete database before exposing its destination name.
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        _publish(temporary, destination)
    except sqlite3.Error as error:
        raise DomainError('invalid_backup', 'The private backup violates schema or reference integrity. No database was restored.', 422) from error
    finally:
        if db is not None:
            db.close()
        temporary.unlink(missing_ok=True)
        for suffix in ('-journal', '-wal', '-shm'):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


# Fixed table descriptors, never client-provided SQL. The hash covers only authored
# content, so edited mutable drafts can be reviewed afresh without changing evidence.
CONTENT = {
    'question_contexts': ('question_id', ('reason',)),
    'drafting_clarification_rounds': ('batch_id', ('reason',)),
    'clarification_round_questions': ('question_id', ('reason',)),
    'question_deferrals': ('id', ('reason',)),
    'programs': ('id', ('title', 'purpose', 'local_context')),
    'program_documents': ('document_key', ('content',)),
    'additional_documents': ('id', ('title', 'sections_json', 'structured_json', 'permission_basis', 'license', 'notices')),
    'additional_document_revisions': ('document_id', ('snapshot_json',)),
    'organizer_clarifications': ('id', ('text',)),
    'proposals': ('id', ('title', 'text', 'theme')),
    'decisions': ('id', ('outcome', 'rationale')),
    'dispositions': ('id', ('reason',)),
    'materials': ('id', ('title', 'content', 'permission_basis', 'license', 'notices')),
    'material_versions': ('id', ('title', 'content', 'permission_basis', 'license', 'notices')),
    'material_improvement_proposals': ('id', ('note', 'proposed_content')),
    'review_drafts': ('id', ('snapshot',)),
    'release_candidates': ('id', ('snapshot',)),
    'releases': ('id', ('snapshot',)),
    'imported_packages': ('program_id', ('source_json',)),
    'imported_material_sources': ('material_id', ('source_json',)),
    'publications': ('id', ('source_json',)),
}


def _content(row, fields):
    result = []
    for field in fields:
        if field not in row.keys():
            continue
        value = row[field]
        if isinstance(value, str) and field in ('content', 'sections_json', 'structured_json', 'snapshot_json', 'snapshot', 'source_json', 'proposed_content'):
            value = json.loads(value)
            if field=='snapshot_json' and isinstance(value,dict):
                value={key:json.loads(child) if key in {'structured_json','sections_json'} and child else child for key,child in value.items()}
        result.append(value)
    return result


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _program(connection, table, row):
    if table=='additional_document_revisions':
        return connection.execute('SELECT program_id FROM additional_documents WHERE id=?',(row['document_id'],)).fetchone()[0]
    if table == 'clarification_round_questions':
        return connection.execute('SELECT program_id FROM drafting_clarification_rounds WHERE batch_id=?', (row['batch_id'],)).fetchone()[0]
    if table == 'programs':
        return row['id']
    if table == 'material_versions':
        return connection.execute('SELECT program_id FROM materials WHERE id=?', (row['material_id'],)).fetchone()[0]
    if table == 'material_improvement_proposals':
        return row['derivative_program_id']
    if table == 'dispositions':
        return connection.execute('SELECT s.program_id FROM responses r JOIN submissions s ON s.id=r.submission_id WHERE r.id=?', (row['response_id'],)).fetchone()[0]
    return row['program_id']


def quarantined(table, row, program_id):
    key, fields = CONTENT[table]
    content = _content(row, fields)
    if get_db().execute('SELECT 1 FROM quarantined_content WHERE table_name=? AND record_key=? AND program_id=? AND content_hash=?',
                        (table, str(row[key]), program_id, digest(content))).fetchone():
        return True
    # Retain hashes of copied authored fields, not the original capture text. A
    # title-only edit or a new ID cannot make unchanged known copied text publishable.
    return contains_quarantined_copy(content)


def contains_quarantined_copy(projection):
    """Check only the content about to be disclosed, including inherited terms."""
    current_hashes = {digest(text) for text in _strings(projection)}
    return any(current_hashes.intersection(json.loads(item[0])) for item in get_db().execute('SELECT field_hashes FROM quarantined_content'))


def ensure_program_publishable(program_id):
    db = get_db()
    for table in ('programs', 'program_documents'):
        sql = 'SELECT * FROM programs WHERE id=?' if table == 'programs' else f'SELECT * FROM {table} WHERE program_id=?'
        if any(quarantined(table, dict(row), program_id) for row in db.execute(sql, (program_id,))):
            raise DomainError('quarantined_content', 'Known copied personal text needs owner review and new approved content before publication.', 409)
    for row in db.execute('SELECT * FROM decisions WHERE program_id=?', (program_id,)):
        if not quarantined('decisions', dict(row), program_id):
            continue
        if not _retired_copied_decision(program_id, dict(row)):
            raise DomainError('quarantined_content', 'A decision contains known copied personal text. Record a clean decision that explicitly supersedes it before publication.', 409)
        if db.execute('SELECT 1 FROM document_decisions WHERE program_id=? AND decision_id=?', (program_id, row['id'])).fetchone():
            raise DomainError('retired_decision_support', 'A document still cites a retired decision containing removed input. Explicitly replace its supporting decision link with reviewed evidence or remove that link before preparing a candidate.', 409)
    for row in db.execute('SELECT mv.* FROM program_materials pm JOIN material_versions mv ON mv.id=pm.version_id WHERE pm.program_id=?', (program_id,)):
        if db.execute("SELECT 1 FROM quarantined_content WHERE table_name='material_versions' AND record_key=?", (row['id'],)).fetchone():
            raise DomainError('quarantined_content', 'An adopted material version contains removed input. Choose a reviewed replacement version.', 409)


def _retired_copied_decision(program_id, decision):
    # The old row remains restricted history. Only explicit same-program
    # supersession retires copied evidence from a newly prepared candidate.
    return quarantined('decisions', decision, program_id) and bool(get_db().execute(
        'SELECT 1 FROM decisions WHERE program_id=? AND supersedes_id=?', (program_id, decision['id'])).fetchone())


def select_candidate_decisions(program_id, decisions):
    """Preserve clean historical evidence; omit explicitly retired copied rows."""
    return [decision for decision in decisions if not _retired_copied_decision(program_id, decision)]


def release_available(release_id, snapshot):
    db = get_db()
    if db.execute('SELECT 1 FROM withdrawn_releases WHERE release_id=?', (release_id,)).fetchone():
        return False
    return not any(db.execute("SELECT 1 FROM quarantined_content WHERE table_name='material_versions' AND record_key=?", (version,)).fetchone() for version in snapshot['material_version_ids'])


def redact_response(actor: Principal, response_id: str, reason: str) -> Record:
    from onpf.contributions.service import get_response
    with transaction() as db:
        response = get_response(actor, response_id)
        require_role(actor, response['program_id'], {'owner'})
        if not isinstance(reason, str) or reason not in REASONS:
            raise DomainError('invalid_reason', 'Choose a standardized nonpersonal removal reason: ' + ', '.join(REASONS) + '.', 422)
        existing = db.execute('SELECT * FROM redaction_events WHERE response_id=?', (response_id,)).fetchone()
        if existing:
            return _owner_report(dict(existing), actor)
        needles = {value for row in response['history'] for value in (row['text'], row['reason']) if value.strip()}
        if response['display_name']:
            needles.add(response['display_name'])
        now, event_id = utcnow(), str(uuid4())
        affected, withdrawn, version_ids = [], [], []
        for table, (key, fields) in CONTENT.items():
            for item in db.execute(f'SELECT * FROM {table}').fetchall():
                row = dict(item)
                content = _content(row, fields)
                if not any(needle in text for text in _strings(content) for needle in needles):
                    continue
                program_id = _program(db, table, row)
                field_hashes = sorted({digest(text) for text in _strings(content) if any(needle in text for needle in needles)})
                affected.append({'table': table, 'record_key': str(row[key]), 'program_id': program_id, 'content_hash': digest(content), 'field_hashes': field_hashes})
                if table == 'releases':
                    withdrawn.append(row['id'])
                elif table == 'material_versions':
                    version_ids.append(row['id'])
                elif table == 'review_drafts':
                    db.execute('UPDATE review_drafts SET revoked_at=?,token_hash=? WHERE id=?', (now, 'disabled:' + row['id'], row['id']))
        # Include downstream frozen selections even when content was edited or copied
        # by an adapter; original provenance/approvals are never rewritten.
        for item in db.execute('SELECT id,snapshot FROM releases'):
            snap = json.loads(item['snapshot'])
            if any(version in version_ids for version in snap['material_version_ids']) and item['id'] not in withdrawn:
                withdrawn.append(item['id'])
        report = {'response_id': response_id, 'event_id': event_id, 'reason': reason,
                  'affected_records': affected, 'withdrawn_release_ids': withdrawn, 'affected_material_version_ids': version_ids,
                  'limitations': 'Exact-text detection is best effort; paraphrases and edited quotations require owner review. Authored copies are reported, not erased. External downloads, prior backups and SQLite storage remnants cannot be recalled by this operation.'}
        db.execute('INSERT INTO redaction_events(id,response_id,program_id,reason,recorded_by,recorded_at,report) VALUES (?,?,?,?,?,?,?)', (event_id, response_id, response['program_id'], reason, actor.user_id, now, encode(report)))
        for record in affected:
            db.execute('INSERT OR IGNORE INTO quarantined_content(table_name,record_key,program_id,content_hash,field_hashes,event_id) VALUES (?,?,?,?,?,?)', (record['table'], record['record_key'], record['program_id'], record['content_hash'], encode(record['field_hashes']), event_id))
        for release in withdrawn:
            db.execute('INSERT OR IGNORE INTO withdrawn_releases(release_id,event_id,withdrawn_at) VALUES (?,?,?)', (release, event_id, now))
        db.execute("UPDATE responses SET text='',display_name=NULL,attribution='anonymous',publication_permission='none',entered_by=NULL,review_required=1 WHERE id=?", (response_id,))
        db.execute("UPDATE response_revisions SET text='',reason='',entered_by=NULL WHERE response_id=?", (response_id,))
        db.execute("UPDATE response_duplicates SET reason='' WHERE response_id=? OR original_id=?", (response_id, response_id))
        db.execute("UPDATE dispositions SET reason='' WHERE response_id=?", (response_id,))
        db.execute("UPDATE submissions SET submission_key=?,payload_hash='' WHERE id=?", ('removed:' + response['submission_id'], response['submission_id']))
        db.execute('UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?', (now, response['program_id']))
        # Serialized with export construction; do not commit withdrawal with stale
        # configured cached packages still on disk. Any error rolls back DB changes.
        directory = Path(current_app.config.get('EXPORT_DIRECTORY', Path(current_app.instance_path) / 'exports'))
        for release in withdrawn:
            (directory / f'onpf-{release}.zip').unlink(missing_ok=True)
        return _owner_report({'report': encode(report), 'program_id': response['program_id']}, actor)


def _owner_report(event, actor):
    report = json.loads(event['report'])
    # The local admin CLI may retrieve the complete report directly. An owner's
    # service result includes only records within their persisted memberships.
    owned = {row[0] for row in get_db().execute("SELECT program_id FROM memberships WHERE user_id=? AND role='owner'", (actor.user_id,))}
    visible = [record for record in report['affected_records'] if record['program_id'] in owned]
    versions = {row[0] for row in get_db().execute('SELECT mv.id FROM material_versions mv JOIN materials m ON m.id=mv.material_id JOIN memberships ms ON ms.program_id=m.program_id WHERE ms.user_id=? AND ms.role=?', (actor.user_id, 'owner'))}
    releases = {row[0] for row in get_db().execute('SELECT r.id FROM releases r JOIN memberships ms ON ms.program_id=r.program_id WHERE ms.user_id=? AND ms.role=?', (actor.user_id, 'owner'))}
    return {**report, 'affected_records': visible, 'other_program_record_count': len(report['affected_records']) - len(visible),
            'affected_material_version_ids': [key for key in report['affected_material_version_ids'] if key in versions],
            'withdrawn_release_ids': [key for key in report['withdrawn_release_ids'] if key in releases]}
