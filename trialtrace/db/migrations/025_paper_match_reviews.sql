-- trialtrace/db/migrations/025_paper_match_reviews.sql
--
-- PURPOSE: A person reviews one model finding about a registered outcome and a paper abstract.
--          The model's finding stays untouched in paper_matches; the review is a separate row.
--          Insert-only history: changing your mind adds a row, the newest row is current.
--          Decisions: accept; correct (a proposed correction, NOT yet verified);
--          accept_correction (explicitly confirms the correction); undetermined (stays unresolved).
--          The row also records the finding as it looked when reviewed and the model/source versions,
--          so a later different answer to the same question is not silently treated as reviewed.

CREATE TABLE IF NOT EXISTS paper_match_reviews (
    id                      BIGSERIAL   PRIMARY KEY,
    match_id                BIGINT      NOT NULL REFERENCES paper_matches (match_id),   -- the exact row reviewed
    input_hash              TEXT        NOT NULL,                  -- the question (outcome + abstract + model setup): finds the review again after a rerun
    decision                TEXT        NOT NULL CHECK (decision IN ('accept', 'correct', 'accept_correction', 'undetermined')),
    reviewer                TEXT        NOT NULL CHECK (reviewer <> ''),
    note                    TEXT,
    corrected_status        TEXT        CHECK (corrected_status IN ('identified', 'not_identified', 'ambiguous')),
    corrected_matched_name  TEXT,
    reviewed_status         TEXT        NOT NULL,                  -- the model finding as the reviewer saw it
    reviewed_matched_name   TEXT,
    reviewed_quote          TEXT,
    model                   TEXT,
    served_model            TEXT,
    prompt_hash             TEXT,
    schema_version          TEXT,
    registry_hash           TEXT        NOT NULL,                  -- the registry version of the outcome
    paper_raw_hash          TEXT        NOT NULL,                  -- the saved paper file the abstract came from
    reviewed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (decision NOT IN ('correct', 'accept_correction') OR corrected_status IS NOT NULL),
    CHECK (decision NOT IN ('correct', 'undetermined') OR (note IS NOT NULL AND note <> ''))     -- these need a reason
);

CREATE INDEX IF NOT EXISTS paper_match_reviews_by_question ON paper_match_reviews (input_hash, id DESC);
