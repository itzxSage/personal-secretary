CREATE TABLE IF NOT EXISTS canonical_identities (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS canonical_life_records (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS canonical_conversations (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS canonical_conversation_events (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS channel_bindings (
    record_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
    content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (record_id, version)
);
CREATE TABLE IF NOT EXISTS migration_manifests (
    manifest_id TEXT PRIMARY KEY,
    source_schema_version INTEGER NOT NULL,
    target_schema_version INTEGER NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS canonical_identities_no_update BEFORE UPDATE ON canonical_identities BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS canonical_life_records_no_update BEFORE UPDATE ON canonical_life_records BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS canonical_conversations_no_update BEFORE UPDATE ON canonical_conversations BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS canonical_conversation_events_no_update BEFORE UPDATE ON canonical_conversation_events BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS channel_bindings_no_update BEFORE UPDATE ON channel_bindings BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS migration_manifests_no_update BEFORE UPDATE ON migration_manifests BEGIN SELECT RAISE(ABORT, 'append only'); END;

CREATE TRIGGER IF NOT EXISTS canonical_identities_guard_delete BEFORE DELETE ON canonical_identities WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS canonical_life_records_guard_delete BEFORE DELETE ON canonical_life_records WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS canonical_conversations_guard_delete BEFORE DELETE ON canonical_conversations WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS canonical_conversation_events_guard_delete BEFORE DELETE ON canonical_conversation_events WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS channel_bindings_guard_delete BEFORE DELETE ON channel_bindings WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;
CREATE TRIGGER IF NOT EXISTS migration_manifests_guard_delete BEFORE DELETE ON migration_manifests WHEN lifecycle_authorized() != 1 BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;