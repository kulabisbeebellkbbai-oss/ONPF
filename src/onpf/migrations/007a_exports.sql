-- Public imports preserve identity-free source evidence; imported authority is never used.
CREATE TABLE imported_packages (
    program_id TEXT PRIMARY KEY REFERENCES programs(id),
    source_json TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    imported_by TEXT NOT NULL REFERENCES users(id),
    imported_at TEXT NOT NULL
);
CREATE TABLE imported_material_sources (
    material_id TEXT PRIMARY KEY REFERENCES materials(id),
    program_id TEXT NOT NULL REFERENCES imported_packages(program_id),
    source_json TEXT NOT NULL
);
