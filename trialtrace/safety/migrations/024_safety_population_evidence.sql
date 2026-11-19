-- Keep subgroup semantics, seriousness and overlapping evidence through reporting.
ALTER TABLE safety_statements
    ADD COLUMN population TEXT,
    ADD COLUMN population_scope_unverified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN event_scope TEXT NOT NULL DEFAULT 'unspecified'
        CHECK (event_scope IN ('unspecified', 'serious', 'non_serious', 'all')),
    ADD COLUMN evidence_group TEXT,
    ADD COLUMN detail_level TEXT NOT NULL DEFAULT 'unspecified'
        CHECK (detail_level IN ('total', 'component', 'unspecified')),
    ADD COLUMN validation_notes TEXT[] NOT NULL DEFAULT '{}';
