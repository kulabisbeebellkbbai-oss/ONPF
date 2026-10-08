CREATE TABLE decision_proposal_snapshots (
    decision_id TEXT NOT NULL REFERENCES decisions(id),
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    proposal_revision INTEGER NOT NULL CHECK (proposal_revision >= 1),
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    theme TEXT NOT NULL,
    capture_basis TEXT NOT NULL CHECK (capture_basis IN ('at_decision','migration_current')),
    captured_at TEXT NOT NULL,
    PRIMARY KEY(decision_id,proposal_id)
);

INSERT INTO decision_proposal_snapshots(decision_id,proposal_id,proposal_revision,title,text,theme,capture_basis,captured_at)
SELECT l.decision_id,l.proposal_id,p.revision,p.title,p.text,p.theme,'migration_current',
       strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
FROM decision_proposals l JOIN proposals p ON p.id=l.proposal_id;
