CREATE TABLE contact_sent_log (
    message_type TEXT NOT NULL CHECK (message_type IN ('comment','new_workspace','tech_support','workspace_admin','accessibility','privacy','other')),
    sent_at TEXT NOT NULL
);
