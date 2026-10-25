-- A saved index covers every outbox entry up to this id; on load, later entries are replayed.
ALTER TABLE index_blobs ADD COLUMN outbox_id INTEGER NOT NULL DEFAULT 0;
