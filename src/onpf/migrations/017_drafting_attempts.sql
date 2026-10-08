CREATE TABLE drafting_attempts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    started_epoch INTEGER NOT NULL
);
CREATE INDEX drafting_attempts_user_time ON drafting_attempts(user_id,started_epoch);
