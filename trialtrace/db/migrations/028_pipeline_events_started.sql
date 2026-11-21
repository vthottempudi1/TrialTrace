-- trialtrace/db/migrations/028_pipeline_events_started.sql
--
-- PURPOSE: Crash detection. A stage writes a 'started' event on its own connection (committed at once), then a
--          completion event that points back to it with started_id. A 'started' event that no completion points to
--          means the run never reported back: it crashed, was stopped, or is still running.

ALTER TABLE pipeline_events DROP CONSTRAINT IF EXISTS pipeline_events_status_check;
ALTER TABLE pipeline_events ADD CONSTRAINT pipeline_events_status_check
    CHECK (status IN ('started', 'ok', 'warning', 'failed', 'no_eligible'));

ALTER TABLE pipeline_events ADD COLUMN IF NOT EXISTS started_id BIGINT;      -- the 'started' event this completion belongs to

CREATE INDEX IF NOT EXISTS pipeline_events_by_start ON pipeline_events (started_id);
