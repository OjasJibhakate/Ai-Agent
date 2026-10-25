-- Small key/value store for housekeeping state (for example, when maintenance last ran).
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
