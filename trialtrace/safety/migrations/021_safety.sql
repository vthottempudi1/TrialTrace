-- trialtrace/db/migrations/021_safety.sql
--
-- PURPOSE: Store the registry's adverse-event module (treatment groups, listed events and their counts) for
--          each saved version of a trial. Safety, step S3. Same rules as the other registry tables:
--          one row per value per saved version, never overwritten, a missing number stays NULL (unknown).
--          safety_status: 'ok' = module present, 'absent' = the record has none, NULL = never extracted (unknown).
--          safety_module holds the per-version facts (frequency threshold, time frame, description).
--          Duplicate event rows are kept (position is part of the key); event counts keep their own order.

ALTER TABLE snapshots
    ADD COLUMN IF NOT EXISTS safety_status TEXT CHECK (safety_status IN ('ok', 'absent'));

CREATE TABLE IF NOT EXISTS safety_module (
    nct_id              TEXT NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash        TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    frequency_threshold DOUBLE PRECISION,                 -- percent; NULL = not given; 0 = every event listed
    time_frame          TEXT,
    description         TEXT,
    PRIMARY KEY (nct_id, content_hash),
    FOREIGN KEY (nct_id, content_hash) REFERENCES snapshots (nct_id, content_hash)
);

CREATE TABLE IF NOT EXISTS safety_arms (
    nct_id           TEXT    NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash     TEXT    NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    position         INTEGER NOT NULL CHECK (position >= 0),        -- index in eventGroups
    group_id         TEXT    NOT NULL CHECK (group_id <> ''),
    title            TEXT    NOT NULL CHECK (title <> ''),
    description      TEXT,
    deaths_affected  INTEGER CHECK (deaths_affected >= 0),
    deaths_at_risk   INTEGER CHECK (deaths_at_risk >= 0),
    serious_affected INTEGER CHECK (serious_affected >= 0),
    serious_at_risk  INTEGER CHECK (serious_at_risk >= 0),
    other_affected   INTEGER CHECK (other_affected >= 0),
    other_at_risk    INTEGER CHECK (other_at_risk >= 0),
    looks_pooled     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (nct_id, content_hash, position),
    FOREIGN KEY (nct_id, content_hash) REFERENCES snapshots (nct_id, content_hash)
);

CREATE TABLE IF NOT EXISTS safety_events (
    nct_id          TEXT    NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    content_hash    TEXT    NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    kind            TEXT    NOT NULL CHECK (kind IN ('serious', 'other')),
    position        INTEGER NOT NULL CHECK (position >= 0),         -- index within its own list
    term            TEXT,                                           -- NULL = the row has no term
    notes           TEXT,
    organ_system    TEXT,
    vocabulary      TEXT,
    assessment_type TEXT CHECK (assessment_type IN ('systematic', 'non_systematic')),
    PRIMARY KEY (nct_id, content_hash, kind, position),
    FOREIGN KEY (nct_id, content_hash) REFERENCES snapshots (nct_id, content_hash)
);

CREATE TABLE IF NOT EXISTS safety_event_counts (
    nct_id         TEXT    NOT NULL,
    content_hash   TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    event_position INTEGER NOT NULL,
    position       INTEGER NOT NULL CHECK (position >= 0),          -- order of the stat inside the event
    group_id       TEXT    NOT NULL CHECK (group_id <> ''),
    affected       INTEGER CHECK (affected >= 0),
    at_risk        INTEGER CHECK (at_risk >= 0),
    events         INTEGER CHECK (events >= 0),
    PRIMARY KEY (nct_id, content_hash, kind, event_position, position),
    FOREIGN KEY (nct_id, content_hash, kind, event_position)
        REFERENCES safety_events (nct_id, content_hash, kind, position)
);

-- The view is built from s.*, so rebuild it to include safety_status.
DROP VIEW IF EXISTS latest_snapshots;

CREATE VIEW latest_snapshots AS
SELECT DISTINCT ON (o.nct_id) s.*, o.observed_at AS last_observed_at
FROM observations o
JOIN snapshots s ON s.nct_id = o.nct_id AND s.content_hash = o.content_hash
ORDER BY o.nct_id, o.observed_at DESC;
