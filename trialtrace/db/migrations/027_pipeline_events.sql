-- trialtrace/db/migrations/027_pipeline_events.sql
--
-- PURPOSE: One row per attempt of a pipeline stage for one trial (insert-only), so "where did this NCT ID get stuck?"
--          can be answered from the database. The facts themselves stay in their own tables; this table adds what
--          they cannot say: that an attempt happened, how long it took, the error code, and the analysis (run) ID.
--          Written by trialtrace/pipeline_events.py. A failure to write an event never stops the pipeline.

CREATE TABLE IF NOT EXISTS pipeline_events (
    id           BIGSERIAL   PRIMARY KEY,
    nct_id       TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    analysis_id  TEXT,                                          -- the match run ID, when the stage belongs to one
    stage        TEXT        NOT NULL CHECK (stage IN ('registry', 'raw_backup', 'papers', 'matching')),
    status       TEXT        NOT NULL CHECK (status IN ('ok', 'warning', 'failed', 'no_eligible')),
    error_code   TEXT,
    detail       JSONB       NOT NULL DEFAULT '{}'::jsonb,      -- small counts, for example pairs, calls, reused
    duration_ms  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pipeline_events_by_trial ON pipeline_events (nct_id, created_at DESC);
