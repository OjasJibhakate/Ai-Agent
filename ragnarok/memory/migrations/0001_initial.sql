-- M1 schema (docs/MEMORY.md section 3.1), plus the outbox and index blob from D-019/D-020.
-- Foreign keys cascade so a hard delete (M5) removes every row derived from a memory.

CREATE TABLE projects (
  id    INTEGER PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

CREATE TABLE events (
  id           INTEGER PRIMARY KEY,
  ts           TEXT NOT NULL,
  source       TEXT NOT NULL,            -- adapter: chat, tool, file, trigger, voice, screen
  kind         TEXT NOT NULL,
  payload      TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  session_id   TEXT,
  expires_at   TEXT                      -- retention window; NULL = keep
);
CREATE INDEX events_ts           ON events(ts);
CREATE INDEX events_content_hash ON events(content_hash, ts);
CREATE INDEX events_expires_at   ON events(expires_at);

CREATE TABLE memories (
  id               INTEGER PRIMARY KEY,   -- also the FTS5 rowid and the Turbovec external id
  content          TEXT    NOT NULL,      -- one self-contained statement
  memory_type      TEXT    NOT NULL CHECK (memory_type IN
                   ('episodic','semantic','preference','procedural','project','summary')),
  origin           TEXT    NOT NULL CHECK (origin IN ('user_stated','observed','inferred')),
  confidence       REAL    NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  importance       REAL    NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
  source           TEXT    NOT NULL,
  project_id       INTEGER REFERENCES projects(id) ON DELETE SET NULL,
  sensitivity      TEXT    NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','private','secret')),
  status           TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','superseded')),
  supersedes_id    INTEGER REFERENCES memories(id) ON DELETE SET NULL,
  valid_from       TEXT,
  valid_to         TEXT,
  pinned           INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),
  created_at       TEXT    NOT NULL,
  last_accessed_at TEXT,
  access_count     INTEGER NOT NULL DEFAULT 0,
  content_hash     TEXT    NOT NULL
);
CREATE INDEX memories_status       ON memories(status);
CREATE INDEX memories_content_hash ON memories(content_hash);
CREATE INDEX memories_project      ON memories(project_id);

CREATE TABLE memory_sources (
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  event_id  INTEGER NOT NULL REFERENCES events(id)   ON DELETE CASCADE,
  PRIMARY KEY (memory_id, event_id)
);

CREATE TABLE memory_links (
  from_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  to_id    INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  relation TEXT    NOT NULL CHECK (relation IN ('derived_from','related','contradicts')),
  PRIMARY KEY (from_id, to_id, relation)
);

CREATE TABLE entities (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL
);

CREATE TABLE memory_entities (
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  PRIMARY KEY (memory_id, entity_id)
);

CREATE TABLE memory_tags (
  memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  tag       TEXT    NOT NULL,
  PRIMARY KEY (memory_id, tag)
);

CREATE TABLE embeddings (
  memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
  model     TEXT    NOT NULL,
  dim       INTEGER NOT NULL,
  vector    BLOB    NOT NULL              -- float32, little-endian, dim * 4 bytes
);

-- External-content FTS5 index over memories.content. Triggers keep it in step with the table.
CREATE VIRTUAL TABLE memories_fts USING fts5(
  content, content='memories', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER memories_fts_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER memories_fts_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER memories_fts_au AFTER UPDATE OF content ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

-- Index operations recorded in the same transaction as the memory write; replayed on startup.
CREATE TABLE index_outbox (
  id         INTEGER PRIMARY KEY,
  op         TEXT    NOT NULL CHECK (op IN ('add','remove')),
  memory_id  INTEGER NOT NULL,           -- no FK: a 'remove' must outlive the deleted row
  created_at TEXT    NOT NULL,
  applied_at TEXT
);
CREATE INDEX index_outbox_pending ON index_outbox(applied_at) WHERE applied_at IS NULL;

-- Serialized Turbovec index (D-020). The embeddings table stays the ground truth.
CREATE TABLE index_blobs (
  name       TEXT PRIMARY KEY,
  bits       INTEGER NOT NULL,
  dim        INTEGER NOT NULL,
  count      INTEGER NOT NULL,
  data       BLOB    NOT NULL,
  updated_at TEXT    NOT NULL
);

-- Content-free record of deletions and other sensitive actions (ids and times only).
CREATE TABLE audit_log (
  id     INTEGER PRIMARY KEY,
  ts     TEXT NOT NULL,
  action TEXT NOT NULL,                  -- e.g. memory_deleted
  ref_id INTEGER
);
