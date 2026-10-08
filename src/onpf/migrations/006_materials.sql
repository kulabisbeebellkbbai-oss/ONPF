CREATE TABLE materials (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    ownership_basis TEXT NOT NULL CHECK (ownership_basis IN ('own_work','third_party')),
    permission_basis TEXT NOT NULL,
    license TEXT NOT NULL,
    notices TEXT NOT NULL,
    source_version_id TEXT REFERENCES material_versions(id),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id,program_id)
);
CREATE TABLE material_versions (
    id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES materials(id),
    version_number INTEGER NOT NULL CHECK (version_number >= 1),
    material_revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    ownership_basis TEXT NOT NULL CHECK (ownership_basis IN ('own_work','third_party')),
    permission_basis TEXT NOT NULL,
    license TEXT NOT NULL,
    notices TEXT NOT NULL,
    source_version_id TEXT REFERENCES material_versions(id),
    content_hash TEXT NOT NULL,
    released_by TEXT NOT NULL REFERENCES users(id),
    released_at TEXT NOT NULL,
    UNIQUE (material_id,version_number),
    UNIQUE (material_id,material_revision),
    UNIQUE (id,material_id)
);
CREATE TABLE program_materials (
    program_id TEXT NOT NULL REFERENCES programs(id),
    material_id TEXT NOT NULL REFERENCES materials(id),
    version_id TEXT NOT NULL,
    adopted_by TEXT NOT NULL REFERENCES users(id),
    adopted_at TEXT NOT NULL,
    PRIMARY KEY (program_id,material_id),
    FOREIGN KEY (version_id,material_id) REFERENCES material_versions(id,material_id)
);
CREATE TABLE material_improvement_proposals (
    id TEXT PRIMARY KEY,
    source_program_id TEXT NOT NULL REFERENCES programs(id),
    source_version_id TEXT NOT NULL REFERENCES material_versions(id),
    derivative_program_id TEXT NOT NULL REFERENCES programs(id),
    derivative_id TEXT NOT NULL,
    derivative_revision INTEGER NOT NULL,
    note TEXT NOT NULL,
    proposed_content TEXT,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    FOREIGN KEY (derivative_id,derivative_program_id) REFERENCES materials(id,program_id)
);
CREATE TRIGGER material_versions_no_update BEFORE UPDATE ON material_versions BEGIN
    SELECT RAISE(ABORT,'Released material versions are immutable');
END;
CREATE TRIGGER material_versions_no_delete BEFORE DELETE ON material_versions BEGIN
    SELECT RAISE(ABORT,'Released material versions are immutable');
END;
