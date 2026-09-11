CREATE TABLE IF NOT EXISTS learning_observations (
    observation_id TEXT PRIMARY KEY,
    retain_until TEXT,
    content_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS learning_observations_retention ON learning_observations(retain_until);
CREATE TRIGGER IF NOT EXISTS learning_observations_no_update
BEFORE UPDATE ON learning_observations BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS learning_observations_guard_delete
BEFORE DELETE ON learning_observations WHEN lifecycle_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;

CREATE TABLE IF NOT EXISTS learning_proposals (
    proposal_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS learning_proposals_status ON learning_proposals(status);
CREATE TRIGGER IF NOT EXISTS learning_proposals_no_update
BEFORE UPDATE ON learning_proposals BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS learning_proposals_guard_delete
BEFORE DELETE ON learning_proposals WHEN lifecycle_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;

CREATE TABLE IF NOT EXISTS learning_tombstones (
    proposal_id TEXT PRIMARY KEY,
    removed_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    pattern_fingerprint TEXT NOT NULL
);