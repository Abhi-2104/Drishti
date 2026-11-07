"""SQLite schema for the per-install store (plan section 6). Typed per-dataset tables are added by each
provider through Store.ensure_table(); this file holds the shared core."""
from __future__ import annotations

SCHEMA_VERSION = 1

# One raw blob per (dataset, trade_date): the latest fetch wins. The row doubles as the "already fetched" marker.
DDL = """
CREATE TABLE IF NOT EXISTS raw_blob (
    dataset     TEXT    NOT NULL,
    trade_date  TEXT    NOT NULL,           -- ISO yyyy-mm-dd, or 'latest' for undated files
    source      TEXT    NOT NULL,
    url         TEXT    NOT NULL,
    fetched_at  TEXT    NOT NULL,           -- UTC ISO
    bytes       INTEGER NOT NULL,
    sha256      TEXT    NOT NULL,
    body        BLOB,
    PRIMARY KEY (dataset, trade_date)
);

CREATE TABLE IF NOT EXISTS signal (
    name        TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    value       TEXT NOT NULL,              -- JSON
    computed_at TEXT NOT NULL,
    PRIMARY KEY (name, as_of)
);

CREATE TABLE IF NOT EXISTS job_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    dataset     TEXT,
    trade_date  TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    error_kind  TEXT,
    detail      TEXT,
    rows        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_job_log_job ON job_log (job, dataset, id);

CREATE TABLE IF NOT EXISTS canary (
    name       TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    newest     TEXT,                        -- newest record date the check saw
    error_kind TEXT,
    detail     TEXT
);

-- ISIN is the join key for everything (NSE, BSE, any broker adapter).
CREATE TABLE IF NOT EXISTS security (
    isin       TEXT PRIMARY KEY,
    name       TEXT,
    nse_symbol TEXT,                        -- NULL = not listed on NSE (BSE-only names are common)
    bse_scrip  TEXT,
    bse_symbol TEXT,
    source     TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_security_nse ON security (nse_symbol);
CREATE INDEX IF NOT EXISTS ix_security_bse ON security (bse_scrip);
"""
