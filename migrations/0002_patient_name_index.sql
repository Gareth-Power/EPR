-- EPR schema v2
-- Speed up the case-insensitive name sort/lookup on the patient list.
CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name COLLATE NOCASE);
