-- trialtrace/db/migrations/015_paper_match_skipped.sql
--
-- PURPOSE: Record the listed papers a match run did NOT read (because of the per-trial paper limit),
--          so a reviewer can always see what was left out. Without this a trial's "not identified"
--          could be mistaken for "not found in any of its papers". Insert-only, one row per run.

CREATE TABLE IF NOT EXISTS paper_match_skipped (
    match_run_id    TEXT        NOT NULL,
    nct_id          TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    pmid            TEXT        NOT NULL,
    paper_raw_hash  TEXT        NOT NULL,
    title           TEXT,
    reason          TEXT        NOT NULL CHECK (reason IN ('paper_limit')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_run_id, nct_id, pmid, paper_raw_hash)
);
