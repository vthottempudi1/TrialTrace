-- trialtrace/db/migrations/010_paper_searches.sql
--
-- PURPOSE: One row each time we searched PubMed for a trial, so "searched and nothing
--          matched" (not_found) differs from "never searched" (no row) and from
--          "could not search" (unavailable / invalid). Insert-only history, like observations.
--          batches_failed counts paper batches we could not fetch or read; the raw files
--          and papers of the batches that worked are still saved.
--          status 'ok' can still be partial: read batches_failed too (the view's
--          answered_fully is true only when the search answered AND every batch was read).

CREATE TABLE IF NOT EXISTS paper_searches (
    search_id      BIGSERIAL   PRIMARY KEY,
    nct_id         TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    query          TEXT        NOT NULL,                       -- the exact search text sent, for auditing
    searched_at    TIMESTAMPTZ NOT NULL,
    status         TEXT        NOT NULL CHECK (status IN ('ok', 'not_found', 'unavailable', 'invalid')),
    total_count    INTEGER,                                    -- how many PubMed says match; NULL = unknown
    pmids_found    INTEGER     NOT NULL DEFAULT 0,             -- how many paper IDs we kept
    truncated      BOOLEAN     NOT NULL DEFAULT false,
    attempts       INTEGER     NOT NULL CHECK (attempts >= 1), -- search attempts
    batches_total  INTEGER     NOT NULL DEFAULT 0,
    batches_failed INTEGER     NOT NULL DEFAULT 0,
    error_code     TEXT                                        -- search error, or the last batch error
);

CREATE INDEX IF NOT EXISTS paper_searches_by_trial ON paper_searches (nct_id, searched_at);

CREATE OR REPLACE VIEW latest_paper_searches AS
SELECT DISTINCT ON (nct_id) *,
       (status IN ('ok', 'not_found') AND batches_failed = 0) AS answered_fully
FROM paper_searches
ORDER BY nct_id, searched_at DESC, search_id DESC;
