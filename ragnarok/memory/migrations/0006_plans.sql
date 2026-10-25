-- Plans (FR3): the goal, its steps, each step's state and result, and a log of what happened.
-- Kept in the vault, apart from the event stream, so a plan survives a crash with its exact state
-- (NFR3.2) and does not expire or get fed to the memory learner.

CREATE TABLE plans (
  id         TEXT PRIMARY KEY,
  goal       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'running' CHECK (status IN
             ('running', 'done', 'partial', 'failed', 'interrupted', 'cancelled')),
  plan       TEXT NOT NULL,                  -- the current steps as JSON (a re-plan replaces it)
  replans    INTEGER NOT NULL DEFAULT 0,
  session_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE plan_steps (
  plan_id     TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  step_id     TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending' CHECK (state IN
              ('pending', 'running', 'waiting_approval', 'done', 'failed', 'blocked',
               'skipped', 'interrupted')),
  attempts    INTEGER NOT NULL DEFAULT 0,
  output      TEXT NOT NULL DEFAULT '',      -- what the step reported, for the steps after it
  error       TEXT NOT NULL DEFAULT '',
  started_at  TEXT,
  finished_at TEXT,
  PRIMARY KEY (plan_id, step_id)
);

-- Append-only: one row per thing that happened, so the plan can be replayed and explained.
CREATE TABLE plan_log (
  seq     INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL,
  kind    TEXT NOT NULL,                      -- created | step_state | retry | replan | finished
  step_id TEXT,
  detail  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX plans_status ON plans(status);
CREATE INDEX plan_log_plan ON plan_log(plan_id, seq);
