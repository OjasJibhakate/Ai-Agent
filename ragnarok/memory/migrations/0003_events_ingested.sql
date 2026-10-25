-- Extraction (M3) works through events that have not been ingested yet.
ALTER TABLE events ADD COLUMN ingested_at TEXT;
CREATE INDEX events_pending ON events(id) WHERE ingested_at IS NULL;
