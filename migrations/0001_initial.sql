-- EPR schema v1
-- (connection pragmas such as foreign_keys / journal_mode are set in db.connect)

CREATE TABLE IF NOT EXISTS patients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    meta        TEXT    NOT NULL DEFAULT '{}',   -- JSON object, key order preserved (list columns)
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sheets (
    patient_id  INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,                -- details | flowsheets | results | notes | mar
    grid        TEXT    NOT NULL DEFAULT '[]',   -- JSON 2D array of strings
    PRIMARY KEY (patient_id, name)
);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    filename    TEXT    NOT NULL,
    mime        TEXT    NOT NULL,
    size        INTEGER NOT NULL,
    path        TEXT    NOT NULL,                -- relative to $EPR_DATA_DIR/files/
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- sheets is keyed on (patient_id, name), so patient_id lookups are already indexed.
CREATE INDEX IF NOT EXISTS idx_files_patient ON files(patient_id);
