CREATE TABLE redaction_events (
    id TEXT PRIMARY KEY,
    response_id TEXT NOT NULL UNIQUE REFERENCES responses(id),
    program_id TEXT NOT NULL REFERENCES programs(id),
    reason TEXT NOT NULL CHECK (reason IN ('privacy_request','retention_expired','captured_in_error','legal_requirement')),
    recorded_by TEXT NOT NULL REFERENCES users(id),
    recorded_at TEXT NOT NULL,
    report TEXT NOT NULL
);
CREATE TABLE withdrawn_releases (
    release_id TEXT PRIMARY KEY REFERENCES releases(id),
    event_id TEXT NOT NULL REFERENCES redaction_events(id),
    withdrawn_at TEXT NOT NULL
);
CREATE TABLE quarantined_content (
    table_name TEXT NOT NULL,
    record_key TEXT NOT NULL,
    program_id TEXT NOT NULL REFERENCES programs(id),
    content_hash TEXT NOT NULL,
    field_hashes TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES redaction_events(id),
    PRIMARY KEY (table_name,record_key,content_hash)
);
CREATE TRIGGER redaction_events_no_update BEFORE UPDATE ON redaction_events BEGIN
    SELECT RAISE(ABORT,'Removal events are immutable');
END;
CREATE TRIGGER redaction_events_no_delete BEFORE DELETE ON redaction_events BEGIN
    SELECT RAISE(ABORT,'Removal events are immutable');
END;
