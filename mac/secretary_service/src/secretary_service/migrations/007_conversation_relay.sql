-- Relay metadata and ordering indexes; content lives only in canonical records.
CREATE TABLE IF NOT EXISTS relay_devices (
    device_id TEXT PRIMARY KEY REFERENCES enrolled_devices(device_id),
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relay_conversations (
    conversation_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS relay_events (
    event_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES relay_conversations(conversation_id),
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    UNIQUE(conversation_id, sequence)
);
