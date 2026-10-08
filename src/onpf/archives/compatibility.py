"""Exact historical envelope recovery into an unpublished empty database."""
from onpf.db import apply_migrations
from onpf.archives import validation

OLD_VERSION = '0.1.0'
OLD_MIGRATIONS = ['001_core.sql', '002_programs.sql', '003_inquiries.sql',
                  '004_contributions.sql', '005_refinement.sql', '006_materials.sql',
                  '007_releases.sql', '007a_exports.sql', '008_archives.sql', '009_contact_log.sql']
GARDEN_MIGRATIONS = OLD_MIGRATIONS + [
    '010_refinement_evidence.sql', '011_access_management.sql',
    '012_operating_permissions.sql', '013_clarification_rounds.sql',
    '014_publications.sql', '015_additional_documents.sql',
    '016_decision_proposal_snapshots.sql', '017_drafting_attempts.sql',
]
AI_MIGRATIONS = GARDEN_MIGRATIONS + ['018_ai_drafting.sql']


def restore_supported_envelope(value: dict, destination_connection) -> None:
    """Validate original hashes, load/check history, then migrate in one transaction.

    Caller owns unpublished empty storage and eventual atomic publication. This
    function commits only after all current validation and rolls back any failure.
    Input dictionaries, original table hashes and frozen history are never edited.
    """
    current = validation.schema_versions()
    if not isinstance(value, dict):
        validation.invalid()
    version, names = value.get('application_version'), value.get('schema_versions')
    if (version, names) not in [(OLD_VERSION, OLD_MIGRATIONS),
                               ('0.2.0', GARDEN_MIGRATIONS),
                               ('0.2.0', AI_MIGRATIONS),
                               (validation.APPLICATION_VERSION, current)]:
        validation.invalid()
    db = destination_connection
    if db.in_transaction or db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
        validation.invalid()
    db.execute('BEGIN IMMEDIATE')
    try:
        apply_migrations(db, names)
        tables = validation.validate_envelope(value, db, application_version=version, versions=names)
        db.execute('PRAGMA defer_foreign_keys=ON')
        db.execute('DELETE FROM schema_migrations')
        for table, rows in tables.items():
            if rows:
                columns = list(rows[0])
                names_sql = ','.join('"' + column + '"' for column in columns)
                db.executemany(f'INSERT INTO "{table}" ({names_sql}) VALUES ({",".join("?" for _ in columns)})',
                               [[None if table=='programs' and column=='retired_at' else row[column] for column in columns] for row in rows])
        for program in tables.get('programs',[]):
            if program.get('retired_at'):
                db.execute('UPDATE programs SET retired_at=? WHERE id=?',(program['retired_at'],program['id']))
        if 'ai_usage' in tables:
            from onpf.db import utcnow
            db.execute('UPDATE ai_usage SET state="uncertain",finished_at=? WHERE state="reserved"',(utcnow(),))
        validation.validate_database(db, include_ai='018_ai_drafting.sql' in names)
        if names != current:
            apply_migrations(db, current)
            validation.validate_database(db)
        db.commit()
    except BaseException:
        db.rollback()
        raise
