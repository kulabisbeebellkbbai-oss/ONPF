CREATE TABLE permission_records (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    version INTEGER NOT NULL,
    issuer TEXT NOT NULL,
    effective_on TEXT NOT NULL,
    expires_on TEXT,
    scope TEXT NOT NULL,
    conditions TEXT NOT NULL,
    filed_on TEXT,
    evidence_name TEXT,
    evidence TEXT,
    replaces_id TEXT REFERENCES permission_records(id),
    recorded_by TEXT NOT NULL REFERENCES users(id),
    recorded_at TEXT NOT NULL,
    UNIQUE(program_id,version)
);
CREATE TABLE permission_copies (
    permission_id TEXT NOT NULL REFERENCES permission_records(id),
    recipient_key TEXT NOT NULL,
    recipient_label TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('unknown','delivered','posted','not_required')),
    recorded_on TEXT,
    PRIMARY KEY(permission_id,recipient_key)
);
CREATE TABLE permission_revocations (
    permission_id TEXT PRIMARY KEY REFERENCES permission_records(id),
    revoked_on TEXT NOT NULL,
    reason TEXT NOT NULL,
    recorded_by TEXT NOT NULL REFERENCES users(id),
    recorded_at TEXT NOT NULL
);
CREATE INDEX permission_records_program ON permission_records(program_id,version);
CREATE TRIGGER permission_records_no_update BEFORE UPDATE ON permission_records BEGIN SELECT RAISE(ABORT,'Permission versions are immutable'); END;
CREATE TRIGGER permission_records_no_delete BEFORE DELETE ON permission_records BEGIN SELECT RAISE(ABORT,'Permission versions are immutable'); END;
CREATE TRIGGER permission_copies_no_update BEFORE UPDATE ON permission_copies BEGIN SELECT RAISE(ABORT,'Permission copy history is immutable'); END;
CREATE TRIGGER permission_copies_no_delete BEFORE DELETE ON permission_copies BEGIN SELECT RAISE(ABORT,'Permission copy history is immutable'); END;
CREATE TRIGGER permission_revocations_no_update BEFORE UPDATE ON permission_revocations BEGIN SELECT RAISE(ABORT,'Permission revocations are immutable'); END;
CREATE TRIGGER permission_revocations_no_delete BEFORE DELETE ON permission_revocations BEGIN SELECT RAISE(ABORT,'Permission revocations are immutable'); END;
