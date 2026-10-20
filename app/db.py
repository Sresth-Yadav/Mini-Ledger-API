"""SQLite connection helpers and schema."""
import os
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    currency       TEXT NOT NULL,
    allow_negative INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transfers (
    id             TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL REFERENCES accounts(id),
    destination_id TEXT NOT NULL REFERENCES accounts(id),
    amount         INTEGER NOT NULL CHECK (amount > 0),  -- minor units (paise, cents)
    currency       TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

-- Double-entry: every transfer writes one debit (-) and one credit (+).
-- Balances are never stored; they are always the sum of entries.
CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id TEXT NOT NULL REFERENCES transfers(id),
    account_id  TEXT NOT NULL REFERENCES accounts(id),
    amount      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entries_account ON entries(account_id);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key          TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    status_code  INTEGER NOT NULL,
    response     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""


def db_path() -> str:
    # Read at call time so tests can point at a temporary file.
    return os.environ.get("LEDGER_DB", "ledger.db")


def connect() -> sqlite3.Connection:
    # isolation_level=None -> autocommit; we open transactions explicitly.
    conn = sqlite3.connect(db_path(), timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up front, so two requests with the
    same idempotency key (or competing transfers) are serialized, not interleaved."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
