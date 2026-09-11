CREATE TABLE IF NOT EXISTS authority_consumption (
    namespace TEXT NOT NULL,
    identifier TEXT NOT NULL,
    PRIMARY KEY (namespace, identifier)
);
