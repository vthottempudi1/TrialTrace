-- trialtrace/db/migrations/004_registry_last_update.sql
--
-- PURPOSE: Store the registry's own "last update posted" date next to our
--          capture date. observed_at = when WE downloaded the record;
--          registry_last_update = when the registry says that version was posted.
--          Raw text as the registry gives it; NULL = unknown.

ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS registry_last_update TEXT;

-- The view is built from s.*, so it must be rebuilt to include the new column.
-- DROP + CREATE (not CREATE OR REPLACE): the new column lands in the middle of
-- the view's column list, and Postgres only allows REPLACE if existing columns keep their positions.
DROP VIEW IF EXISTS latest_snapshots;

CREATE VIEW latest_snapshots AS
SELECT DISTINCT ON (o.nct_id) s.*, o.observed_at AS last_observed_at
FROM observations o
JOIN snapshots s ON s.nct_id = o.nct_id AND s.content_hash = o.content_hash
ORDER BY o.nct_id, o.observed_at DESC;
