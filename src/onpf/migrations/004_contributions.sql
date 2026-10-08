CREATE TABLE submissions (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    batch_id TEXT NOT NULL REFERENCES batches(id),
    actor_key TEXT NOT NULL,
    submission_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    submitted_by TEXT REFERENCES users(id),
    invite_id TEXT REFERENCES invitations(id),
    submitted_at TEXT NOT NULL,
    UNIQUE (program_id,actor_key,submission_key),
    UNIQUE (id,batch_id),
    CHECK ((submitted_by IS NULL) != (invite_id IS NULL))
);
CREATE TABLE responses (
    id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    answer_state TEXT NOT NULL CHECK (answer_state IN ('answered','abstain','not_applicable')),
    attribution TEXT NOT NULL CHECK (attribution IN ('named','alias','anonymous','group')),
    display_name TEXT,
    entry_method TEXT NOT NULL CHECK (entry_method IN ('direct','paper','meeting')),
    publication_permission TEXT NOT NULL DEFAULT 'none' CHECK (publication_permission IN ('none','anonymous_quote','attributed_quote')),
    entered_by TEXT REFERENCES users(id),
    entered_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    review_required INTEGER NOT NULL DEFAULT 1 CHECK (review_required IN (0,1)),
    FOREIGN KEY (submission_id,batch_id) REFERENCES submissions(id,batch_id),
    FOREIGN KEY (batch_id,question_id) REFERENCES batch_questions(batch_id,id),
    UNIQUE (submission_id,ordinal),
    CHECK (attribution != 'anonymous' OR display_name IS NULL),
    CHECK (publication_permission != 'attributed_quote' OR (attribution != 'anonymous' AND display_name IS NOT NULL))
);
CREATE TABLE response_revisions (
    id TEXT PRIMARY KEY,
    response_id TEXT NOT NULL REFERENCES responses(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    text TEXT NOT NULL,
    reason TEXT NOT NULL,
    entered_by TEXT REFERENCES users(id),
    entered_at TEXT NOT NULL,
    UNIQUE (response_id,revision)
);
CREATE TABLE response_duplicates (
    id TEXT PRIMARY KEY,
    response_id TEXT NOT NULL REFERENCES responses(id),
    original_id TEXT NOT NULL REFERENCES responses(id),
    reason TEXT NOT NULL,
    entered_by TEXT NOT NULL REFERENCES users(id),
    entered_at TEXT NOT NULL,
    CHECK (response_id != original_id)
);
CREATE INDEX responses_batch ON responses(batch_id,question_id);
CREATE INDEX response_duplicates_response ON response_duplicates(response_id,entered_at);
