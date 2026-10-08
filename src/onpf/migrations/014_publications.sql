CREATE TABLE publications (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    program_revision INTEGER NOT NULL,
    approval_status TEXT NOT NULL CHECK (approval_status IN ('approved','unapproved')),
    source_json TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    published_by TEXT NOT NULL REFERENCES users(id),
    published_at TEXT NOT NULL,
    UNIQUE(program_id,version_number)
);
CREATE INDEX publications_program ON publications(program_id,version_number);
CREATE TRIGGER publications_no_update BEFORE UPDATE ON publications BEGIN SELECT RAISE(ABORT,'Published versions are immutable'); END;
CREATE TRIGGER publications_no_delete BEFORE DELETE ON publications BEGIN SELECT RAISE(ABORT,'Published versions are immutable'); END;
