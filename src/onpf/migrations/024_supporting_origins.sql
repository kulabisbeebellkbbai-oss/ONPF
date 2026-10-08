DROP TRIGGER freeze_ai_origin;
ALTER TABLE ai_origins RENAME TO ai_origins_old;
CREATE TABLE ai_origins (
    id TEXT PRIMARY KEY, program_id TEXT NOT NULL REFERENCES programs(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    target_kind TEXT NOT NULL CHECK(target_kind IN ('questions','proposal','decision','document','supporting')),
    target_key TEXT NOT NULL,request_target TEXT NOT NULL,generated_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,reviewed_hash TEXT NOT NULL,target_hash TEXT NOT NULL,
    model_alias TEXT NOT NULL,generated_at TEXT NOT NULL,saved_at TEXT NOT NULL,
    sources TEXT NOT NULL,saved_sources TEXT NOT NULL
);
INSERT INTO ai_origins SELECT * FROM ai_origins_old;
DROP TABLE ai_origins_old;
CREATE INDEX ai_origins_target ON ai_origins(program_id,target_kind,target_key);
CREATE TRIGGER freeze_ai_origin BEFORE UPDATE ON ai_origins
BEGIN SELECT RAISE(ABORT,'AI origins are append-only'); END;
CREATE TRIGGER ai_origins_no_delete BEFORE DELETE ON ai_origins
BEGIN SELECT RAISE(ABORT,'AI origins are immutable'); END;
