ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1));
CREATE TABLE ai_policies (
    scope TEXT NOT NULL CHECK(scope IN ('system','project','user')),
    target_id TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    limits_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(scope,target_id)
);
CREATE TABLE administrative_events (
    id INTEGER PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES users(id),
    happened_at TEXT NOT NULL,
    action TEXT NOT NULL,
    scope TEXT NOT NULL,
    target_id TEXT NOT NULL,
    changes_json TEXT NOT NULL
);
