CREATE TABLE IF NOT EXISTS relay_retention (
    conversation_id TEXT PRIMARY KEY REFERENCES relay_conversations(conversation_id),
    expires_at TEXT NOT NULL
);
