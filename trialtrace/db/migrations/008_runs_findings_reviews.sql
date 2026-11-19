-- trialtrace/db/migrations/008_runs_findings_reviews.sql
--
-- ---------------------------------------------------------------------------
-- PURPOSE
-- ---------------------------------------------------------------------------
-- Keep every report we generate, and every human decision about it, as history.
--
--   runs      one row each time a report is generated
--   findings  the answers (one Finding per trial per check) belonging to one run
--   reviews   a person's decision about one finding: approved, edited or rejected
--
-- Key rules this migration follows:
--   - Nothing is overwritten. A new report is a new run with new findings; an
--     earlier run stays exactly as it was.
--   - Reviews are insert-only history. A person who changes their mind adds a
--     new review; the old one stays. The CURRENT decision is the newest review
--     (view current_reviews).
--   - A review must say who made it. A rejection must give a reason, and an
--     edit must contain the edited text: the database refuses anything else.
--   - Findings point to their evidence by evidence ID (a list of text). That is
--     not a foreign key, because one finding cites many evidence rows.
--
-- ---------------------------------------------------------------------------
-- HOW THE TABLES CONNECT
-- ---------------------------------------------------------------------------
--   runs 1 --- * findings 1 --- * reviews
--   finding_id looks like "<run_id>:<nct_id>:<check>", for example
--   "run-20261102T203015Z:NCT04644237:timeline".
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT        PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of       DATE        NOT NULL                -- the "today" the time-based checks used
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id    TEXT   PRIMARY KEY,
    run_id        TEXT   NOT NULL REFERENCES runs (run_id),
    nct_id        TEXT   NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    check_name    TEXT   NOT NULL,                   -- which question: results_posted, timeline, registry_changes
    category      TEXT   NOT NULL,                   -- the answer; 'unknown' when we cannot say
    claim         TEXT   NOT NULL,
    evidence_ids  TEXT[] NOT NULL DEFAULT '{}',
    limitations   TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS findings_by_run ON findings (run_id);

CREATE TABLE IF NOT EXISTS reviews (
    id            BIGSERIAL   PRIMARY KEY,
    finding_id    TEXT        NOT NULL REFERENCES findings (finding_id),
    decision      TEXT        NOT NULL CHECK (decision IN ('approved', 'edited', 'rejected')),
    reviewer      TEXT        NOT NULL CHECK (reviewer <> ''),
    reason        TEXT,
    edited_claim  TEXT,
    reviewed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (decision <> 'edited' OR (edited_claim IS NOT NULL AND edited_claim <> '')),
    CHECK (decision <> 'rejected' OR (reason IS NOT NULL AND reason <> ''))
);
CREATE INDEX IF NOT EXISTS reviews_by_finding ON reviews (finding_id, reviewed_at DESC);

-- current_reviews: the newest decision for each finding (older ones stay in reviews as history).
CREATE OR REPLACE VIEW current_reviews AS
SELECT DISTINCT ON (finding_id) *
FROM reviews
ORDER BY finding_id, reviewed_at DESC, id DESC;
