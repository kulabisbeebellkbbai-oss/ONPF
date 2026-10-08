CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE programs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT '',
    local_context TEXT NOT NULL DEFAULT '',
    operating_status TEXT NOT NULL DEFAULT 'pending',
    approval_rule TEXT NOT NULL DEFAULT 'all' CHECK (approval_rule IN ('all','any')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE memberships (
    program_id TEXT NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner','facilitator','contributor')),
    PRIMARY KEY (program_id,user_id)
);
CREATE TABLE auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX auth_sessions_expiry ON auth_sessions(expires_at);
CREATE TABLE login_attempts (
    username TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    blocked_until TEXT NOT NULL
);
