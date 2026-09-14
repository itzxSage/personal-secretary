-- Dedicated customer cell. Run provisioning separately from application startup.
CREATE TABLE IF NOT EXISTS lifeos_cell (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    tenant_id UUID NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    audit_sequence BIGINT NOT NULL DEFAULT 0,
    audit_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifeos_domain (
    kind TEXT NOT NULL,
    record_id UUID NOT NULL,
    version BIGINT NOT NULL CHECK (version > 0),
    envelope TEXT NOT NULL,
    PRIMARY KEY (kind, record_id, version)
);
CREATE TABLE IF NOT EXISTS lifeos_tombstones (
    kind TEXT NOT NULL,
    record_id UUID NOT NULL,
    envelope TEXT NOT NULL,
    PRIMARY KEY (kind, record_id)
);
CREATE TABLE IF NOT EXISTS lifeos_audit (
    sequence BIGINT PRIMARY KEY CHECK (sequence > 0),
    entry_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifeos_consumption (
    namespace TEXT NOT NULL,
    identifier TEXT NOT NULL,
    PRIMARY KEY (namespace, identifier)
);
CREATE TABLE IF NOT EXISTS lifeos_outbox (
    operation_id UUID PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN
        ('queued', 'running', 'uncertain', 'succeeded', 'cancelled', 'expired')),
    envelope TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifeos_devices (
    device_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    envelope TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifeos_relay_identities (
    participant_id UUID PRIMARY KEY,
    envelope TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifeos_relay_devices (
    device_id UUID PRIMARY KEY,
    participant_id UUID NOT NULL REFERENCES lifeos_relay_identities,
    envelope TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifeos_conversations (
    conversation_id UUID PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL,
    envelope TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifeos_conversation_events (
    event_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES lifeos_conversations ON DELETE CASCADE,
    sequence BIGINT NOT NULL CHECK (sequence > 0),
    envelope TEXT NOT NULL,
    UNIQUE (conversation_id, sequence)
);
CREATE TABLE IF NOT EXISTS lifeos_conversation_deletions (
    conversation_id UUID PRIMARY KEY
);
CREATE OR REPLACE FUNCTION lifeos_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable LifeOS record';
END;
$$;
CREATE OR REPLACE TRIGGER lifeos_domain_no_update
BEFORE UPDATE ON lifeos_domain FOR EACH ROW EXECUTE FUNCTION lifeos_reject_mutation();
CREATE OR REPLACE TRIGGER lifeos_audit_immutable
BEFORE UPDATE OR DELETE ON lifeos_audit FOR EACH ROW EXECUTE FUNCTION lifeos_reject_mutation();
CREATE OR REPLACE TRIGGER lifeos_tombstones_immutable
BEFORE UPDATE OR DELETE ON lifeos_tombstones FOR EACH ROW EXECUTE FUNCTION lifeos_reject_mutation();
CREATE OR REPLACE TRIGGER lifeos_consumption_immutable
BEFORE UPDATE OR DELETE ON lifeos_consumption FOR EACH ROW EXECUTE FUNCTION lifeos_reject_mutation();
CREATE OR REPLACE TRIGGER lifeos_conversation_deletions_immutable
BEFORE UPDATE OR DELETE ON lifeos_conversation_deletions
FOR EACH ROW EXECUTE FUNCTION lifeos_reject_mutation();
CREATE OR REPLACE TRIGGER lifeos_conversation_events_no_update
BEFORE UPDATE ON lifeos_conversation_events FOR EACH ROW EXECUTE FUNCTION lifeos_reject_mutation();
