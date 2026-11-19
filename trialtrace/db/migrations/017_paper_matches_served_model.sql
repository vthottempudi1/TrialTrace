-- trialtrace/db/migrations/017_paper_matches_served_model.sql
--
-- PURPOSE: Record the model name the API actually reported for each real LLM call (served_model).
--          The configured name (model) can be an alias that the provider re-points to a newer version.
--          A stored answer is reused only if its served_model equals the model the API reports now
--          (a tiny probe call at the start of each match run), so a silent model change stops old
--          answers from being reused. Rows from before this migration have no served_model and are
--          not reused once the check is on.

ALTER TABLE paper_matches ADD COLUMN IF NOT EXISTS served_model TEXT;
