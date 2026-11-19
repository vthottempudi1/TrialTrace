-- trialtrace/db/migrations/001_core.sql
--
-- PURPOSE: The core tables. Raw files stay on disk; these rows hold the parsed
--          data plus a pointer (content_hash, raw_file_path) back to each file.
-- USE:     python -m trialtrace.db.migrate   (runs every new .sql file once)

-- snapshots: what one registry record said at one download.
-- Same trial, changed content = a new row. Nothing is ever overwritten.
CREATE TABLE IF NOT EXISTS snapshots (
    nct_id                  TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash            TEXT        NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    title                   TEXT        NOT NULL,
    sponsor                 TEXT,                               -- NULL = unknown
    overall_status          TEXT        NOT NULL,
    primary_completion_date TEXT,                               -- raw text: '2019-05' stays '2019-05'
    primary_completion_type TEXT        CHECK (primary_completion_type IN ('ACTUAL', 'ESTIMATED')),
    has_results             BOOLEAN,                            -- true / false / NULL = unknown
    source_url              TEXT        NOT NULL,
    raw_file_path           TEXT        NOT NULL,
    retrieved_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (nct_id, content_hash)
);

-- evidence: one exact value from a saved file, so any claim can cite it.
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id   TEXT        PRIMARY KEY,
    nct_id        TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash  TEXT        NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    source        TEXT        NOT NULL CHECK (source IN ('clinicaltrials', 'pubmed')),
    source_url    TEXT        NOT NULL,
    field_path    TEXT        NOT NULL,
    value         TEXT        NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_by_snapshot ON evidence (nct_id, content_hash);   -- fast lookup of one snapshot's evidence

-- ingest_failures: every trial we could not load, and why (rule 6: never dropped).
CREATE TABLE IF NOT EXISTS ingest_failures (
    id          BIGSERIAL   PRIMARY KEY,
    nct_id      TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    step        TEXT        NOT NULL CHECK (step IN ('fetch', 'parse')),
    error_code  TEXT        NOT NULL,                           -- 'timeout', 'http_500', 'ParseError'...
    attempts    INTEGER     NOT NULL CHECK (attempts >= 1),
    failed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- latest_snapshots: exactly one (newest) row per trial, so counts never double count.
CREATE OR REPLACE VIEW latest_snapshots AS
SELECT DISTINCT ON (nct_id) *
FROM snapshots
ORDER BY nct_id, retrieved_at DESC;
