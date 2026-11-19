-- trialtrace/db/migrations/016_paper_matches_reuse.sql
--
-- PURPOSE: Let a new match run reuse an earlier LLM answer instead of paying for the same call again.
--          input_hash fingerprints everything the model saw (outcome text, time frame, abstract,
--          model, prompt fingerprint, schema version, temperature setting). reused_from points to
--          the row the answer was copied from, so every answer can be traced to a real model call.
--          Old rows (before this migration) have no input_hash and are never reused.

ALTER TABLE paper_matches ADD COLUMN IF NOT EXISTS input_hash  TEXT;
ALTER TABLE paper_matches ADD COLUMN IF NOT EXISTS reused_from BIGINT REFERENCES paper_matches (match_id);

CREATE INDEX IF NOT EXISTS paper_matches_by_input
    ON paper_matches (input_hash, match_id DESC) WHERE system = 'llm' AND input_hash IS NOT NULL;
