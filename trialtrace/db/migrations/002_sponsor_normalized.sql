-- trialtrace/db/migrations/002_sponsor_normalized.sql
--
-- PURPOSE: A shared key for sponsor name variants, so question 3 counts
--          "Acme Pharma, Inc." and "ACME PHARMA INC" as one sponsor.
--          Filled by repository.save_snapshot using parse/sponsor.py.
--          NULL = sponsor unknown (never grouped with other unknowns).

ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS sponsor_normalized TEXT;

-- Re-create the view so it includes the new column (it was built with SELECT *).
CREATE OR REPLACE VIEW latest_snapshots AS
SELECT DISTINCT ON (nct_id) *
FROM snapshots
ORDER BY nct_id, retrieved_at DESC;
