-- Project tasks (FR3.2): a small board the agent can read and update, kept in the encrypted vault.

CREATE TABLE tasks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  project    TEXT NOT NULL DEFAULT 'general',
  title      TEXT NOT NULL,
  due        TEXT,                                   -- YYYY-MM-DD, optional
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
  created_at TEXT NOT NULL,
  done_at    TEXT
);

CREATE INDEX tasks_open ON tasks(status, project);
