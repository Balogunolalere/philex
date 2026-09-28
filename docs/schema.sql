-- Turso schema for philexentertainment.com form submissions.
--
-- This is the table the Worker writes to through src/db.py. It lives in the
-- `philex` database on Turso (libsql://philex-doombuggy.aws-us-east-1.turso.io),
-- group `default`. Run it against a fresh database with:
--
--   turso db shell philex < docs/schema.sql
--
-- One flat row per submission: a column that means nothing to a given form
-- stays empty rather than becoming its own table, and `kind` says which form
-- the row came from (`contact`, `reservation`, `free-ticket`).

CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  kind TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  party_size TEXT NOT NULL DEFAULT '',
  booking_date TEXT NOT NULL DEFAULT '',
  booking_time TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  user_agent TEXT NOT NULL DEFAULT ''
);

-- Keeps the "has this address claimed?" lookup inside the guarded insert cheap
-- as the table grows.
--
-- It is deliberately NOT unique. A unique index is the tidier statement of
-- "one ticket per address", but it cannot be created while rows that predate
-- the rule are in the table, and the duplicates already here are real claims
-- the organisers may still be working from. The insert in src/db.py
-- (INSERT_ONCE_SQL) is what actually enforces the rule: it checks and writes in
-- a single statement, so two claims posted at the same moment cannot both land.
--
-- Once the historical duplicates are resolved, this can become:
--   CREATE UNIQUE INDEX submissions_free_ticket_address_uniq
--     ON submissions (kind, lower(trim(email))) WHERE kind = 'free-ticket';
CREATE INDEX IF NOT EXISTS submissions_free_ticket_address
  ON submissions (kind, lower(trim(email)))
  WHERE kind = 'free-ticket';
