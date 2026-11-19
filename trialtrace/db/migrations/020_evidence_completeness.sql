-- trialtrace/db/migrations/020_evidence_completeness.sql
--
-- PURPOSE: A paper-summary finding says how much of the listed literature was read, apart from its answer:
--          'complete' = every listed paper was read and usable; 'incomplete' = some were left out (limit, stored
--          after the run, abstract changed) or the model's answer for one could not be used.
--          NULL = not a paper summary, or a finding saved before this field existed.

ALTER TABLE findings
    ADD COLUMN IF NOT EXISTS evidence_completeness TEXT CHECK (evidence_completeness IN ('complete', 'incomplete'));
