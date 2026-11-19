-- trialtrace/db/migrations/026_review_evidence_time.sql
--
-- PURPOSE: Assisted review of "unclear" findings records more than a decision: the supporting evidence the reviewer
--          relied on, why a finding stays unclear (a fixed list of reasons, so they can be counted), and how long
--          the reviewer had the finding on screen. All three are optional for older reviews.

ALTER TABLE paper_match_reviews ADD COLUMN IF NOT EXISTS evidence TEXT;
ALTER TABLE paper_match_reviews ADD COLUMN IF NOT EXISTS unresolved_reason TEXT
    CHECK (unresolved_reason IN ('generic_statement', 'subgroup_or_partial', 'different_time_point', 'different_analysis_population',
                                 'wrong_or_uncertain_trial', 'different_measure', 'predictor_only', 'abstract_too_short', 'other'));
ALTER TABLE paper_match_reviews ADD COLUMN IF NOT EXISTS review_seconds INTEGER CHECK (review_seconds >= 0);
ALTER TABLE paper_match_reviews ADD CONSTRAINT paper_match_reviews_undetermined_needs_reason
    CHECK (decision <> 'undetermined' OR unresolved_reason IS NOT NULL) NOT VALID;      -- applies to new rows; older rows are left as they are
