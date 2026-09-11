CREATE TABLE IF NOT EXISTS enrolled_devices (
    device_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    record TEXT NOT NULL
);
