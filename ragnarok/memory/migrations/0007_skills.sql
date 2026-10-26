-- Skill registry (FR2.1): one row per registered skill, with the manifest that was approved and a
-- hash of the skill folder, so a skill whose files change on disk is switched off until the owner
-- approves it again. The skill's files live in <home>/skills/<name>/; only metadata is kept here.

CREATE TABLE skills (
  name         TEXT PRIMARY KEY,
  version      TEXT NOT NULL,
  description  TEXT NOT NULL,
  risk         TEXT NOT NULL,
  manifest     TEXT NOT NULL,                -- skill.json as approved
  content_hash TEXT NOT NULL,                -- sha256 over the folder's files
  enabled      INTEGER NOT NULL DEFAULT 1,
  note         TEXT NOT NULL DEFAULT '',     -- why it is off, if it is
  approved_at  TEXT NOT NULL,
  added_at     TEXT NOT NULL
);
