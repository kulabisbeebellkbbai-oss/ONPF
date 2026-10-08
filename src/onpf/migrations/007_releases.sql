CREATE TABLE release_candidates (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    program_revision INTEGER NOT NULL CHECK (program_revision >= 1),
    content_hash TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    prepared_by TEXT NOT NULL REFERENCES users(id),
    prepared_at TEXT NOT NULL,
    UNIQUE (id,program_id)
);
CREATE TABLE candidate_owners (
    candidate_id TEXT NOT NULL REFERENCES release_candidates(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    PRIMARY KEY (candidate_id,user_id)
);
CREATE TABLE candidate_approvals (
    candidate_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    PRIMARY KEY (candidate_id,user_id),
    FOREIGN KEY (candidate_id,user_id) REFERENCES candidate_owners(candidate_id,user_id)
);
CREATE TABLE releases (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    candidate_id TEXT NOT NULL UNIQUE,
    release_number INTEGER NOT NULL CHECK (release_number >= 1),
    content_hash TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    approvals TEXT NOT NULL,
    released_at TEXT NOT NULL,
    UNIQUE (program_id,release_number),
    FOREIGN KEY (candidate_id,program_id) REFERENCES release_candidates(id,program_id)
);
CREATE TABLE release_material_sources (
    program_id TEXT PRIMARY KEY REFERENCES programs(id),
    material_id TEXT NOT NULL UNIQUE,
    FOREIGN KEY (material_id,program_id) REFERENCES materials(id,program_id)
);
CREATE TABLE release_material_versions (
    release_id TEXT PRIMARY KEY REFERENCES releases(id),
    version_id TEXT NOT NULL UNIQUE REFERENCES material_versions(id)
);
CREATE TRIGGER release_candidates_no_update BEFORE UPDATE ON release_candidates BEGIN
    SELECT RAISE(ABORT,'Prepared candidates are immutable');
END;
CREATE TRIGGER release_candidates_no_delete BEFORE DELETE ON release_candidates BEGIN
    SELECT RAISE(ABORT,'Prepared candidates are immutable');
END;
CREATE TRIGGER candidate_owners_no_update BEFORE UPDATE ON candidate_owners BEGIN
    SELECT RAISE(ABORT,'Candidate owners are immutable');
END;
CREATE TRIGGER candidate_owners_no_delete BEFORE DELETE ON candidate_owners BEGIN
    SELECT RAISE(ABORT,'Candidate owners are immutable');
END;
CREATE TRIGGER candidate_approvals_no_update BEFORE UPDATE ON candidate_approvals BEGIN
    SELECT RAISE(ABORT,'Candidate approvals are immutable');
END;
CREATE TRIGGER candidate_approvals_no_delete BEFORE DELETE ON candidate_approvals BEGIN
    SELECT RAISE(ABORT,'Candidate approvals are immutable');
END;
CREATE TRIGGER releases_no_update BEFORE UPDATE ON releases BEGIN
    SELECT RAISE(ABORT,'Program releases are immutable');
END;
CREATE TRIGGER releases_no_delete BEFORE DELETE ON releases BEGIN
    SELECT RAISE(ABORT,'Program releases are immutable');
END;
