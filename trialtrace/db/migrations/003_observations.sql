-- trialtrace/db/migrations/003_observations.sql
--
-- PURPOSE: Keep the full sequence of what we saw, separate from the content.
--          snapshots = each distinct content once (retrieved_at = first captured).
--          observations = every successful fetch, insert-only.
--          So A -> B -> A leaves three observations, and nothing is overwritten.
--          latest_snapshots now follows the newest OBSERVATION.

CREATE TABLE IF NOT EXISTS observations (
    id            BIGSERIAL   PRIMARY KEY,
    nct_id        TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash  TEXT        NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    observed_at   TIMESTAMPTZ NOT NULL,                       -- when WE downloaded it (capture date)
    UNIQUE (nct_id, content_hash, observed_at),               -- saving the same fetch twice adds nothing
    FOREIGN KEY (nct_id, content_hash) REFERENCES snapshots (nct_id, content_hash)
);
CREATE INDEX IF NOT EXISTS observations_by_trial ON observations (nct_id, observed_at DESC);

-- Existing snapshots become their first observation (the best record we have).
INSERT INTO observations (nct_id, content_hash, observed_at)
SELECT nct_id, content_hash, retrieved_at FROM snapshots
ON CONFLICT DO NOTHING;

-- latest_snapshots: the content we saw MOST RECENTLY, one row per trial.
-- last_observed_at is added at the end of the columns.
CREATE OR REPLACE VIEW latest_snapshots AS
SELECT DISTINCT ON (o.nct_id) s.*, o.observed_at AS last_observed_at
FROM observations o
JOIN snapshots s ON s.nct_id = o.nct_id AND s.content_hash = o.content_hash
ORDER BY o.nct_id, o.observed_at DESC;
