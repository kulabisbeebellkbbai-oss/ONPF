CREATE TABLE proposal_incorporations (
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    response_id TEXT NOT NULL REFERENCES responses(id),
    response_revision_id TEXT NOT NULL,
    recorded_by TEXT NOT NULL REFERENCES users(id),
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (proposal_id,response_id,response_revision_id),
    FOREIGN KEY (response_revision_id,response_id) REFERENCES response_revisions(id,response_id)
);
CREATE INDEX proposal_incorporations_response ON proposal_incorporations(response_id,recorded_at);
