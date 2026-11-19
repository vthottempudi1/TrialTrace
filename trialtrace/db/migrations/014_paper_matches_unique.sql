-- trialtrace/db/migrations/014_paper_matches_unique.sql
--
-- PURPOSE: One prediction per system per outcome per paper in a run. An outcome is identified by its
--          role AND position (positions count within a role). A second insert of the same
--          prediction in the same run fails instead of silently doubling the evidence.

CREATE UNIQUE INDEX IF NOT EXISTS paper_matches_one_per_run
    ON paper_matches (match_run_id, nct_id, outcome_role, outcome_position, pmid, paper_raw_hash, system);
