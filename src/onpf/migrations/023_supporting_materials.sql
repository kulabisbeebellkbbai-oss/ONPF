ALTER TABLE additional_documents ADD COLUMN structured_json TEXT;
ALTER TABLE additional_documents ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE additional_documents ADD COLUMN is_template INTEGER NOT NULL DEFAULT 0;
ALTER TABLE additional_documents ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE additional_documents ADD COLUMN template_source_id TEXT REFERENCES additional_documents(id);
ALTER TABLE additional_documents ADD COLUMN template_source_revision INTEGER;
CREATE TABLE additional_document_revisions (
    document_id TEXT NOT NULL REFERENCES additional_documents(id),
    revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    saved_by TEXT REFERENCES users(id),
    saved_at TEXT NOT NULL,
    PRIMARY KEY(document_id,revision)
);
