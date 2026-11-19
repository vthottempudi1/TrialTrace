-- trialtrace/db/migrations/018_review_unclear_and_source_run.sql
--
-- PURPOSE: 1. A reviewer can answer "unclear": they cannot tell from the evidence. Like a rejection, it
--             needs a reason. (approved / edited / rejected / unclear)
--          2. A finding records which match run it was built from (source_run_id), so the model's
--             original predictions in paper_matches and the reviewer's later corrections in reviews can
--             be followed from one to the other. paper_matches is never changed by a review.

ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_decision_check;
ALTER TABLE reviews ADD CONSTRAINT reviews_decision_check
    CHECK (decision IN ('approved', 'edited', 'rejected', 'unclear'));

ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_unclear_needs_reason;
ALTER TABLE reviews ADD CONSTRAINT reviews_unclear_needs_reason
    CHECK (decision <> 'unclear' OR (reason IS NOT NULL AND reason <> ''));

ALTER TABLE findings ADD COLUMN IF NOT EXISTS source_run_id TEXT;      -- the paper-match run behind a paper finding; NULL otherwise
