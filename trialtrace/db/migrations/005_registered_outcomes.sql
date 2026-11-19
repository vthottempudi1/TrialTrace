-- trialtrace/db/migrations/005_registered_outcomes.sql
--
-- PURPOSE: Store the outcomes each saved version of a trial registered, one row
--          per outcome, plus whether that version had an outcomes section at all.
--          outcomes_status: 'ok' = section present (it may list nothing),
--          'absent' = no section, NULL = never extracted (unknown).

ALTER TABLE snapshots
    ADD COLUMN IF NOT EXISTS outcomes_status TEXT CHECK (outcomes_status IN ('ok', 'absent'));

CREATE TABLE IF NOT EXISTS registered_outcomes (
    nct_id        TEXT    NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash  TEXT    NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    role          TEXT    NOT NULL CHECK (role IN ('primary', 'secondary')),
    position      INTEGER NOT NULL CHECK (position >= 0),                  -- 0 = first in its list
    title         TEXT    NOT NULL CHECK (title <> ''),
    description   TEXT,                                                    -- NULL = not given
    time_frame    TEXT,                                                    -- NULL = not given
    PRIMARY KEY (nct_id, content_hash, role, position),
    FOREIGN KEY (nct_id, content_hash) REFERENCES snapshots (nct_id, content_hash)
);

-- The view is built from s.*, so rebuild it to include outcomes_status.
-- DROP + CREATE: the new column lands in the middle of the column list.
DROP VIEW IF EXISTS latest_snapshots;

CREATE VIEW latest_snapshots AS
SELECT DISTINCT ON (o.nct_id) s.*, o.observed_at AS last_observed_at
FROM observations o
JOIN snapshots s ON s.nct_id = o.nct_id AND s.content_hash = o.content_hash
ORDER BY o.nct_id, o.observed_at DESC;
