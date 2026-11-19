-- trialtrace/db/migrations/019_paper_link_reviews.sql
--
-- PURPOSE: A person says how a paper relates to a trial: it reports the results, describes the protocol,
--          only mentions the trial, or is not about this trial. The code's link check (paper_links) is a
--          guess from IDs and text; this is the human answer, kept beside it and never replacing it.
--          Insert-only history, like reviews: changing your mind adds a row, the newest row is current.
--          A paper is identified by (nct_id, pmid, raw_hash), the same key as paper_links.

CREATE TABLE IF NOT EXISTS paper_link_reviews (
    id            BIGSERIAL   PRIMARY KEY,
    nct_id        TEXT        NOT NULL,
    pmid          TEXT        NOT NULL,
    raw_hash      TEXT        NOT NULL,
    relationship  TEXT        NOT NULL CHECK (relationship IN ('reports_results', 'describes_protocol', 'mentions_only', 'not_this_trial')),
    reviewer      TEXT        NOT NULL CHECK (reviewer <> ''),
    reason        TEXT,
    reviewed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (nct_id, pmid, raw_hash) REFERENCES paper_links (nct_id, pmid, raw_hash),
    CHECK (relationship <> 'not_this_trial' OR (reason IS NOT NULL AND reason <> ''))      -- excluding a paper needs a reason
);

CREATE INDEX IF NOT EXISTS paper_link_reviews_by_trial ON paper_link_reviews (nct_id, pmid, raw_hash, reviewed_at DESC);

-- current_paper_link_reviews: the newest answer for each paper of each trial
CREATE OR REPLACE VIEW current_paper_link_reviews AS
SELECT DISTINCT ON (nct_id, pmid, raw_hash) *
FROM paper_link_reviews
ORDER BY nct_id, pmid, raw_hash, reviewed_at DESC, id DESC;
