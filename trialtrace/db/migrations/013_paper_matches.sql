-- trialtrace/db/migrations/013_paper_matches.sql
--
-- PURPOSE: Store what each system said about ONE registered outcome in ONE paper's abstract.
--          The LLM and the baseline are stored as separate rows and never merged, so a person can
--          always see both predictions. Insert-only: a later run adds rows and never changes old ones.
--          Everything is provisional until a human reviews it. A status of 'failed' keeps the reason
--          (error_code); a missing prediction is never turned into "not identified".

CREATE TABLE IF NOT EXISTS paper_matches (
    match_id           BIGSERIAL   PRIMARY KEY,
    match_run_id       TEXT        NOT NULL,                  -- one run of the match-papers step
    nct_id             TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    registry_hash      TEXT        NOT NULL,                  -- the registry version the outcome came from
    outcome_position   INTEGER     NOT NULL,
    outcome_role       TEXT        NOT NULL,
    outcome_text       TEXT        NOT NULL,
    pmid               TEXT        NOT NULL,
    paper_raw_hash     TEXT        NOT NULL,
    system             TEXT        NOT NULL CHECK (system IN ('baseline', 'llm')),
    model              TEXT,                                  -- the LLM model; NULL for the baseline
    prompt_hash        TEXT,
    schema_version     TEXT,
    status             TEXT        NOT NULL CHECK (status IN ('identified', 'not_identified', 'ambiguous', 'no_text', 'failed')),
    reported_role      TEXT        CHECK (reported_role IN ('primary', 'secondary')),
    matched_name       TEXT,
    quote_text         TEXT,
    quote_start        INTEGER,
    quote_end          INTEGER,
    error_code         TEXT,
    reason             TEXT,
    raw_response       TEXT,                                  -- the model's answer exactly as received
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    seconds            DOUBLE PRECISION,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((quote_text IS NULL) = (quote_start IS NULL) AND (quote_start IS NULL) = (quote_end IS NULL))
);

CREATE INDEX IF NOT EXISTS paper_matches_by_trial ON paper_matches (nct_id, match_run_id);
