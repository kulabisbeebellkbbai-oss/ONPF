CREATE TABLE organizer_clarifications (
    id TEXT NOT NULL,
    program_id TEXT NOT NULL REFERENCES programs(id),
    revision INTEGER NOT NULL,
    text TEXT NOT NULL,
    context_json TEXT NOT NULL,
    author_id TEXT NOT NULL REFERENCES users(id),
    saved_at TEXT NOT NULL,
    PRIMARY KEY (id,revision)
);
