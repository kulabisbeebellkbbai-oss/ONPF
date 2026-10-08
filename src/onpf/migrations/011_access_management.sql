ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1));
ALTER TABLE users ADD COLUMN can_create_projects INTEGER NOT NULL DEFAULT 1 CHECK (can_create_projects IN (0,1));
UPDATE users SET is_admin=1 WHERE id=(SELECT id FROM users ORDER BY created_at,rowid LIMIT 1);
CREATE TABLE memberships_new (
    program_id TEXT NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner','facilitator','contributor','viewer')),
    PRIMARY KEY (program_id,user_id)
);
INSERT INTO memberships_new(program_id,user_id,role) SELECT program_id,user_id,role FROM memberships;
DROP TABLE memberships;
ALTER TABLE memberships_new RENAME TO memberships;
