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
