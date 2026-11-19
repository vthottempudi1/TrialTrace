-- trialtrace/db/migrations/007_outcome_measure_text.sql
--
-- PURPOSE: Rename registered_outcomes.title to measure_text, so it is not confused
--          with the trial's own title (snapshots.title). It holds the registry's
--          "measure" text for one outcome, kept exactly as written.
--          The "not empty" rule on the column carries over with the rename.

ALTER TABLE registered_outcomes RENAME COLUMN title TO measure_text;
