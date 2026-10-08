ALTER TABLE programs ADD COLUMN retired_at TEXT;
ALTER TABLE programs ADD COLUMN retired_by TEXT REFERENCES users(id);
ALTER TABLE programs ADD COLUMN public_from_version INTEGER NOT NULL DEFAULT 1;
CREATE TABLE project_retirement_events (
    id INTEGER PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    actor_id TEXT NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    happened_at TEXT NOT NULL
);
