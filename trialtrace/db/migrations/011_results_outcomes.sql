-- trialtrace/db/migrations/011_results_outcomes.sql
--
-- PURPOSE: Store the outcomes each saved version of a trial REPORTED in its posted results,
--          one row per outcome (the "structured results" arm), plus whether that version
--          had a results measures section at all.
--          results_status: 'ok' = section present (it may list nothing),
--          'absent' = no results measures posted, NULL = never extracted (unknown).
--          Kept apart from registered_outcomes: that table is the plan, this one is the report.

ALTER TABLE snapshots
    ADD COLUMN IF NOT EXISTS results_status TEXT CHECK (results_status IN ('ok', 'absent'));

CREATE TABLE IF NOT EXISTS results_outcomes (
    nct_id           TEXT    NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash     TEXT    NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    position         INTEGER NOT NULL CHECK (position >= 0),                 -- index in the results list
    result_type      TEXT    CHECK (result_type IN ('primary', 'secondary', 'other', 'post_hoc')),   -- NULL = unknown
    measure_text     TEXT    NOT NULL CHECK (measure_text <> ''),
    description      TEXT,                                                   -- NULL = not given
    time_frame       TEXT,
    population       TEXT,
    reporting_status TEXT,
    PRIMARY KEY (nct_id, content_hash, position),
    FOREIGN KEY (nct_id, content_hash) REFERENCES snapshots (nct_id, content_hash)
);

-- The view is built from s.*, so rebuild it to include results_status.
DROP VIEW IF EXISTS latest_snapshots;

CREATE VIEW latest_snapshots AS
SELECT DISTINCT ON (o.nct_id) s.*, o.observed_at AS last_observed_at
FROM observations o
JOIN snapshots s ON s.nct_id = o.nct_id AND s.content_hash = o.content_hash
ORDER BY o.nct_id, o.observed_at DESC;
