-- Triggers (FR4): reminders, repeating schedules and folder watches, their fire history, and the Inbox.
-- Everything lives in the encrypted vault, so a restart (or a crash) loses nothing.

CREATE TABLE triggers (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT NOT NULL CHECK (kind IN ('date', 'cron', 'folder')),
  spec          TEXT NOT NULL,                  -- ISO time (date), cron rule (cron), folder path (folder)
  glob          TEXT NOT NULL DEFAULT '',       -- folder triggers: which file names count
  action        TEXT NOT NULL CHECK (action IN ('notify', 'goal')),
  payload       TEXT NOT NULL,                  -- the reminder text, or the goal to work on
  label         TEXT NOT NULL DEFAULT '',       -- the schedule as said back to the user
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done', 'cancelled')),
  state         TEXT NOT NULL DEFAULT '{}',     -- folder snapshot and similar cursors (JSON)
  created_at    TEXT NOT NULL,
  last_fired_at TEXT
);

-- One row per fire. The unique key is what makes "each event fires once, even across restarts" true.
CREATE TABLE trigger_fires (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_id INTEGER NOT NULL REFERENCES triggers(id) ON DELETE CASCADE,
  dedupe_key TEXT NOT NULL,
  fired_at   TEXT NOT NULL,
  outcome    TEXT NOT NULL DEFAULT 'started' CHECK (outcome IN ('started', 'done', 'failed')),
  UNIQUE (trigger_id, dedupe_key)
);

CREATE TABLE inbox (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  fire_id    INTEGER UNIQUE REFERENCES trigger_fires(id) ON DELETE SET NULL,
  trigger_id INTEGER REFERENCES triggers(id) ON DELETE SET NULL,
  kind       TEXT NOT NULL,                     -- reminder | result | file | failed
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT 'unread' CHECK (status IN ('unread', 'read', 'dismissed'))
);

CREATE INDEX triggers_active ON triggers(status, kind);
CREATE INDEX inbox_status ON inbox(status, id);
