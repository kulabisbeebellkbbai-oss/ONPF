CREATE TABLE additional_documents (
    id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    template_key TEXT NOT NULL,
    title TEXT NOT NULL,
    sections_json TEXT NOT NULL,
    ownership_basis TEXT NOT NULL CHECK (ownership_basis IN ('own_work','third_party')),
    permission_basis TEXT NOT NULL,
    license TEXT NOT NULL,
    notices TEXT NOT NULL,
    include_in_public INTEGER NOT NULL DEFAULT 0 CHECK (include_in_public IN (0,1)),
    upload_name TEXT,
    upload_mime TEXT,
    upload_data TEXT,
    upload_sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((upload_name IS NULL AND upload_mime IS NULL AND upload_data IS NULL AND upload_sha256 IS NULL)
        OR (upload_name IS NOT NULL AND upload_mime IS NOT NULL AND upload_data IS NOT NULL AND upload_sha256 IS NOT NULL))
);
CREATE INDEX additional_documents_program ON additional_documents(program_id,created_at);
