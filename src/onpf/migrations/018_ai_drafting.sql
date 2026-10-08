CREATE TABLE ai_origins (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('questions','proposal','decision','document')),
    target_key TEXT NOT NULL,
    request_target TEXT NOT NULL,
    generated_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    reviewed_hash TEXT NOT NULL,
    target_hash TEXT NOT NULL,
    model_alias TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    sources TEXT NOT NULL,
    saved_sources TEXT NOT NULL
);
CREATE INDEX ai_origins_target ON ai_origins(program_id,target_kind,target_key);
CREATE TRIGGER freeze_ai_origin BEFORE UPDATE ON ai_origins
BEGIN SELECT RAISE(ABORT,'AI origins are append-only'); END;
CREATE TABLE question_contexts (
    program_id TEXT NOT NULL REFERENCES programs(id),
    question_id TEXT NOT NULL,
    context_revision INTEGER NOT NULL DEFAULT 1 CHECK (context_revision >= 1),
    reason TEXT NOT NULL,
    sources TEXT NOT NULL,
    updated_by TEXT NOT NULL REFERENCES users(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (program_id,question_id)
);
CREATE UNIQUE INDEX clarification_batch_program ON batches(id,program_id);
CREATE TABLE drafting_clarification_rounds (
    batch_id TEXT PRIMARY KEY REFERENCES batches(id),
    program_id TEXT NOT NULL REFERENCES programs(id),
    stage INTEGER CHECK (stage BETWEEN 1 AND 7),
    kind TEXT NOT NULL CHECK (kind IN ('initial','clarification')),
    reason TEXT NOT NULL,
    sources TEXT NOT NULL,
    issued_by TEXT NOT NULL REFERENCES users(id),
    issued_at TEXT NOT NULL,
    UNIQUE (batch_id,program_id),
    FOREIGN KEY (batch_id,program_id) REFERENCES batches(id,program_id)
);
CREATE TABLE clarification_round_questions (
    question_id TEXT PRIMARY KEY REFERENCES batch_questions(id),
    batch_id TEXT NOT NULL REFERENCES drafting_clarification_rounds(batch_id),
    reason TEXT NOT NULL,
    sources TEXT NOT NULL,
    UNIQUE (batch_id,question_id),
    FOREIGN KEY (batch_id,question_id) REFERENCES batch_questions(batch_id,id)
);
CREATE TABLE question_deferrals (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    batch_id TEXT NOT NULL REFERENCES drafting_clarification_rounds(batch_id),
    question_id TEXT NOT NULL REFERENCES clarification_round_questions(question_id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    deferred_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    FOREIGN KEY (batch_id,program_id) REFERENCES drafting_clarification_rounds(batch_id,program_id),
    FOREIGN KEY (batch_id,question_id) REFERENCES clarification_round_questions(batch_id,question_id)
);
CREATE INDEX question_deferrals_scope ON question_deferrals(program_id,batch_id,question_id);
CREATE TRIGGER freeze_drafting_clarification_round BEFORE UPDATE ON drafting_clarification_rounds
BEGIN SELECT RAISE(ABORT,'Issued clarification rounds are immutable'); END;
CREATE TRIGGER retain_drafting_clarification_round BEFORE DELETE ON drafting_clarification_rounds
BEGIN SELECT RAISE(ABORT,'Issued clarification rounds are immutable'); END;
CREATE TRIGGER freeze_clarification_context BEFORE UPDATE ON clarification_round_questions
BEGIN SELECT RAISE(ABORT,'Issued clarification context is immutable'); END;
CREATE TRIGGER retain_clarification_context BEFORE DELETE ON clarification_round_questions
BEGIN SELECT RAISE(ABORT,'Issued clarification context is immutable'); END;
CREATE TRIGGER freeze_question_deferral BEFORE UPDATE ON question_deferrals
BEGIN SELECT RAISE(ABORT,'Question deferrals are append-only'); END;
CREATE TRIGGER retain_question_deferral BEFORE DELETE ON question_deferrals
BEGIN SELECT RAISE(ABORT,'Question deferrals are append-only'); END;
CREATE TRIGGER retain_ai_origin BEFORE DELETE ON ai_origins
BEGIN SELECT RAISE(ABORT,'AI origins are append-only'); END;
