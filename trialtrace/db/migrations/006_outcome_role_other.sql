-- trialtrace/db/migrations/006_outcome_role_other.sql
--
-- PURPOSE: The registry has a third outcome list, otherOutcomes. Allow role 'other'
--          so those outcomes are stored instead of silently ignored.

ALTER TABLE registered_outcomes DROP CONSTRAINT IF EXISTS registered_outcomes_role_check;
ALTER TABLE registered_outcomes
    ADD CONSTRAINT registered_outcomes_role_check CHECK (role IN ('primary', 'secondary', 'other'));
