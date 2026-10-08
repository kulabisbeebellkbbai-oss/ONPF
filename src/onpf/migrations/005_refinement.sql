CREATE TABLE proposals (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    theme TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id,program_id)
);
CREATE UNIQUE INDEX response_revision_identity ON response_revisions(id,response_id);
CREATE TABLE proposal_responses (
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    response_id TEXT NOT NULL REFERENCES responses(id),
    response_revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (proposal_id,response_id),
    FOREIGN KEY (response_revision_id,response_id) REFERENCES response_revisions(id,response_id)
);
CREATE TABLE dispositions (
    id TEXT PRIMARY KEY,
    response_id TEXT NOT NULL REFERENCES responses(id),
    response_revision_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('unreviewed','incorporated','adapted','deferred','declined')),
    reason TEXT NOT NULL,
    entered_by TEXT NOT NULL REFERENCES users(id),
    entered_at TEXT NOT NULL,
    FOREIGN KEY (response_revision_id,response_id) REFERENCES response_revisions(id,response_id)
);
CREATE TABLE disposition_proposals (
    disposition_id TEXT NOT NULL REFERENCES dispositions(id),
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (disposition_id,proposal_id)
);
CREATE TABLE decisions (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    outcome TEXT NOT NULL,
    rationale TEXT NOT NULL,
    supersedes_id TEXT UNIQUE,
    entered_by TEXT NOT NULL REFERENCES users(id),
    entered_at TEXT NOT NULL,
    UNIQUE (id,program_id),
    FOREIGN KEY (supersedes_id,program_id) REFERENCES decisions(id,program_id)
);
CREATE TABLE decision_proposals (
    decision_id TEXT NOT NULL REFERENCES decisions(id),
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (decision_id,proposal_id)
);
CREATE TABLE decision_responses (
    decision_id TEXT NOT NULL REFERENCES decisions(id),
    response_id TEXT NOT NULL REFERENCES responses(id),
    response_revision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (decision_id,response_id),
    FOREIGN KEY (response_revision_id,response_id) REFERENCES response_revisions(id,response_id)
);
CREATE TABLE document_decisions (
    program_id TEXT NOT NULL,
    document_key TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (program_id,document_key,decision_id),
    FOREIGN KEY (program_id,document_key) REFERENCES program_documents(program_id,document_key),
    FOREIGN KEY (decision_id,program_id) REFERENCES decisions(id,program_id)
);
CREATE TABLE review_drafts (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    program_revision INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX dispositions_response ON dispositions(response_id,entered_at);
