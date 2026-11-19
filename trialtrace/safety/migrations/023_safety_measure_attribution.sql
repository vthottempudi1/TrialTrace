-- Preserve measure semantics and local attribution evidence across saved reports.
-- Existing rows keep their historical participant-proportion interpretation.
ALTER TABLE safety_statements
    ADD COLUMN measure TEXT NOT NULL DEFAULT 'participant_proportion'
        CHECK (measure IN ('participant_proportion', 'time_to_event')),
    ADD COLUMN attribution_quote TEXT;
