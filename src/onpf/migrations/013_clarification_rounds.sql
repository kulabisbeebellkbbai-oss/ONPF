CREATE TABLE clarification_rounds (
    batch_id TEXT PRIMARY KEY REFERENCES batches(id),
    program_id TEXT NOT NULL REFERENCES programs(id),
    round_number INTEGER NOT NULL CHECK (round_number >= 1),
    after_step TEXT NOT NULL,
    purpose TEXT NOT NULL,
    UNIQUE(program_id, round_number)
);
CREATE TABLE clarification_question_sources (
    question_id TEXT PRIMARY KEY REFERENCES batch_questions(id),
    source_type TEXT NOT NULL CHECK (source_type IN ('program','response','proposal','decision','document')),
    source_id TEXT NOT NULL,
    why TEXT NOT NULL,
    group_label TEXT NOT NULL DEFAULT ''
);
CREATE TABLE clarification_deferrals (
    question_id TEXT PRIMARY KEY REFERENCES batch_questions(id),
    reason TEXT NOT NULL,
    deferred_by TEXT NOT NULL REFERENCES users(id),
    deferred_at TEXT NOT NULL
);
CREATE INDEX clarification_rounds_program ON clarification_rounds(program_id,round_number);
