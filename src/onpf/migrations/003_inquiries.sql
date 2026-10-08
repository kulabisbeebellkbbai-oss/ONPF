CREATE TABLE decision_fields (
    program_id TEXT NOT NULL REFERENCES programs(id),
    key TEXT NOT NULL,
    label TEXT NOT NULL,
    value TEXT,
    depends_on TEXT NOT NULL DEFAULT '[]',
    decided_by TEXT REFERENCES users(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (program_id,key)
);
CREATE TABLE inquiry_questions (
    program_id TEXT NOT NULL REFERENCES programs(id),
    id TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (program_id,id)
);
CREATE TABLE batches (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    title TEXT NOT NULL,
    instructions TEXT NOT NULL,
    target_group TEXT NOT NULL,
    due_date TEXT,
    intake_copy TEXT NOT NULL,
    framework_version TEXT NOT NULL,
    issued_by TEXT NOT NULL REFERENCES users(id),
    issued_at TEXT NOT NULL,
    closed_at TEXT,
    UNIQUE (program_id,version)
);
CREATE TABLE batch_questions (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(id),
    source_id TEXT NOT NULL,
    stage INTEGER NOT NULL,
    text TEXT NOT NULL,
    answer_type TEXT NOT NULL,
    document_key TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    override_reason TEXT NOT NULL DEFAULT '',
    ordinal INTEGER NOT NULL,
    UNIQUE (batch_id,source_id),
    UNIQUE (batch_id,ordinal),
    UNIQUE (batch_id,id)
);
CREATE TABLE invitations (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(id),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TRIGGER freeze_batch_question BEFORE UPDATE ON batch_questions
BEGIN SELECT RAISE(ABORT,'Issued questions are immutable'); END;
CREATE TRIGGER freeze_batch BEFORE UPDATE ON batches
WHEN NEW.id IS NOT OLD.id OR NEW.program_id IS NOT OLD.program_id OR NEW.version IS NOT OLD.version
 OR NEW.title IS NOT OLD.title OR NEW.instructions IS NOT OLD.instructions OR NEW.target_group IS NOT OLD.target_group
 OR NEW.due_date IS NOT OLD.due_date OR NEW.intake_copy IS NOT OLD.intake_copy
 OR NEW.framework_version IS NOT OLD.framework_version OR NEW.issued_by IS NOT OLD.issued_by OR NEW.issued_at IS NOT OLD.issued_at
 OR (OLD.closed_at IS NOT NULL AND NEW.closed_at IS NOT OLD.closed_at)
BEGIN SELECT RAISE(ABORT,'Issued batches are immutable and cannot reopen'); END;
