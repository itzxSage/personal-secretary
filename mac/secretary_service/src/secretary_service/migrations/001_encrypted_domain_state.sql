CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identities (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS connectors (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS capabilities (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS source_items (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS normalized_facts (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS tasks (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS events (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS proposals (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS approvals (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS executions (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS energy_check_ins (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS goals (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS pattern_snapshots (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS consent_records (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);

CREATE TRIGGER IF NOT EXISTS identities_no_update BEFORE UPDATE ON identities BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS connectors_no_update BEFORE UPDATE ON connectors BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS capabilities_no_update BEFORE UPDATE ON capabilities BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS source_items_no_update BEFORE UPDATE ON source_items BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS normalized_facts_no_update BEFORE UPDATE ON normalized_facts BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS tasks_no_update BEFORE UPDATE ON tasks BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS proposals_no_update BEFORE UPDATE ON proposals BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS approvals_no_update BEFORE UPDATE ON approvals BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS executions_no_update BEFORE UPDATE ON executions BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS energy_check_ins_no_update BEFORE UPDATE ON energy_check_ins BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS goals_no_update BEFORE UPDATE ON goals BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS pattern_snapshots_no_update BEFORE UPDATE ON pattern_snapshots BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS consent_records_no_update BEFORE UPDATE ON consent_records BEGIN SELECT RAISE(ABORT, 'append only'); END;

CREATE TRIGGER IF NOT EXISTS identities_guard_delete BEFORE DELETE ON identities WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS connectors_guard_delete BEFORE DELETE ON connectors WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS capabilities_guard_delete BEFORE DELETE ON capabilities WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS source_items_guard_delete BEFORE DELETE ON source_items WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS normalized_facts_guard_delete BEFORE DELETE ON normalized_facts WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS tasks_guard_delete BEFORE DELETE ON tasks WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS events_guard_delete BEFORE DELETE ON events WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS proposals_guard_delete BEFORE DELETE ON proposals WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS approvals_guard_delete BEFORE DELETE ON approvals WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS executions_guard_delete BEFORE DELETE ON executions WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS energy_check_ins_guard_delete BEFORE DELETE ON energy_check_ins WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS goals_guard_delete BEFORE DELETE ON goals WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS pattern_snapshots_guard_delete BEFORE DELETE ON pattern_snapshots WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS consent_records_guard_delete BEFORE DELETE ON consent_records WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;

CREATE TABLE IF NOT EXISTS audit_entries (
    sequence INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    action_class TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    action_metadata TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_chain_anchor (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    sequence INTEGER NOT NULL,
    entry_hash TEXT NOT NULL
);
INSERT OR IGNORE INTO audit_chain_anchor VALUES(1, 0, '0000000000000000000000000000000000000000000000000000000000000000');
CREATE TRIGGER IF NOT EXISTS audit_entries_no_update
BEFORE UPDATE ON audit_entries BEGIN SELECT RAISE(ABORT, 'audit entries are immutable'); END;
CREATE TRIGGER IF NOT EXISTS audit_entries_no_delete
BEFORE DELETE ON audit_entries WHEN lifecycle_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'audit entries are immutable'); END;

CREATE TABLE IF NOT EXISTS tombstones (
    record_id TEXT PRIMARY KEY,
    record_kind TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backup_manifests (
    backup_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    wrapped_data_key BLOB,
    nonce BLOB,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backup_records (
    backup_id TEXT NOT NULL REFERENCES backup_manifests(backup_id),
    record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL,
    PRIMARY KEY (backup_id, record_kind, record_id)
);
