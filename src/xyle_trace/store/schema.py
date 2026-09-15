SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS nodes (
    project_id  TEXT NOT NULL,
    id          TEXT NOT NULL,
    type        TEXT NOT NULL,
    natural_key TEXT NOT NULL,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, id)
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes (project_id, type);

CREATE TABLE IF NOT EXISTS edges (
    project_id TEXT NOT NULL,
    id         TEXT NOT NULL,
    type       TEXT NOT NULL,
    src        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, id)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges (project_id, src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges (project_id, dst);

CREATE TABLE IF NOT EXISTS records (
    project_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (project_id, dataset_id, key)
);

CREATE TABLE IF NOT EXISTS resolutions (
    project_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    record_key TEXT NOT NULL,
    status     TEXT NOT NULL,
    target_id  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, dataset_id, record_key)
);
"""
