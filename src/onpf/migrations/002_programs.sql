ALTER TABLE programs ADD COLUMN module_keys TEXT NOT NULL DEFAULT '[]';
CREATE TABLE program_documents (
    program_id TEXT NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    document_key TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (program_id,document_key)
);
