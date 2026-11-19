-- trialtrace/db/migrations/012_finding_arm_method.sql
--
-- PURPOSE: A finding can say what was compared with the registry (comparison_arm) and how
--          outcomes were matched (match_method), so evaluation can split precision by
--          evidence quality. NULL = not a registry-versus-reported comparison, or a finding
--          saved before these fields existed.

ALTER TABLE findings
    ADD COLUMN IF NOT EXISTS comparison_arm TEXT CHECK (comparison_arm IN ('results_submission', 'full_text', 'abstract')),
    ADD COLUMN IF NOT EXISTS match_method   TEXT CHECK (match_method IN ('deterministic', 'semantic'));
