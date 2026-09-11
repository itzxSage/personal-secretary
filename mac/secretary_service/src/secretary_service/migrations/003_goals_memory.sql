CREATE TABLE IF NOT EXISTS goal_graph_nodes (
    record_id TEXT PRIMARY KEY,
    node_kind TEXT NOT NULL,
    parent_id TEXT,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goal_graph_nodes_parent ON goal_graph_nodes(parent_id);
CREATE TRIGGER IF NOT EXISTS goal_graph_nodes_no_update
BEFORE UPDATE ON goal_graph_nodes BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS goal_graph_nodes_guard_delete
BEFORE DELETE ON goal_graph_nodes WHEN lifecycle_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;

CREATE TABLE IF NOT EXISTS governed_memories (
    memory_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    retain_until TEXT,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS governed_memories_retention ON governed_memories(retain_until);
CREATE TRIGGER IF NOT EXISTS governed_memories_no_update
BEFORE UPDATE ON governed_memories BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER IF NOT EXISTS governed_memories_guard_delete
BEFORE DELETE ON governed_memories WHEN lifecycle_authorized() != 1
BEGIN SELECT RAISE(ABORT, 'lifecycle required'); END;

CREATE TABLE IF NOT EXISTS memory_tombstones (
    memory_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    removed_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    PRIMARY KEY (memory_id, revision)
);
