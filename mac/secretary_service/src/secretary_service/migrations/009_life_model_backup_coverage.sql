-- Mark backups whose inventory includes governed memories, not only domain records.
CREATE TABLE IF NOT EXISTS backup_memory_coverage (
    backup_id TEXT PRIMARY KEY REFERENCES backup_manifests(backup_id)
);
