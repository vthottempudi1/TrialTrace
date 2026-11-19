-- trialtrace/db/migrations/022_safety_readings.sql
--
-- PURPOSE: Store what each system read about safety in ONE paper's abstract. Safety, step S6.
--          safety_readings   one row per paper, per system (baseline or llm), per run: the whole answer
--          safety_statements one row per statement in that answer, passed or rejected
--          The model and the baseline are stored separately and never merged. Insert-only: a later run
--          adds rows and never changes old ones. A rejected statement keeps the reason (rejected_code)
--          and is never deleted. Nothing here is compared with the registry yet (that is step S7).
--          input_hash fingerprints everything the model saw, so a stored answer can be reused instead of
--          paid for again; reused_from points to the reading that came from the real model call.

CREATE TABLE IF NOT EXISTS safety_readings (
    reading_id      BIGSERIAL   PRIMARY KEY,
    safety_run_id   TEXT        NOT NULL,                  -- one run of the read-safety step
    nct_id          TEXT        NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),   -- the trial the paper was read for
    pmid            TEXT        NOT NULL,
    paper_raw_hash  TEXT        NOT NULL,
    system          TEXT        NOT NULL CHECK (system IN ('baseline', 'llm')),
    model           TEXT,                                  -- the configured model; NULL for the baseline
    served_model    TEXT,                                  -- the model name the API reported for the real call
    prompt_hash     TEXT,
    schema_version  TEXT,
    status          TEXT        NOT NULL CHECK (status IN ('ok', 'no_text', 'failed')),
    document        TEXT,                                  -- what kind of paper the model says it is
    error_code      TEXT,                                  -- why the whole answer failed
    reason          TEXT,
    raw_response    TEXT,                                  -- the model's answer exactly as received
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    seconds         DOUBLE PRECISION,
    input_hash      TEXT,
    reused_from     BIGINT      REFERENCES safety_readings (reading_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (safety_run_id, nct_id, pmid, system)
);

CREATE INDEX IF NOT EXISTS safety_readings_by_trial ON safety_readings (nct_id, safety_run_id);
CREATE INDEX IF NOT EXISTS safety_readings_by_input
    ON safety_readings (input_hash, reading_id DESC) WHERE system = 'llm' AND input_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS safety_statements (
    reading_id        BIGINT  NOT NULL REFERENCES safety_readings (reading_id),
    position          INTEGER NOT NULL CHECK (position >= 0),      -- order in the answer (passed first, then rejected)
    kind              TEXT    CHECK (kind IN ('death', 'serious', 'event')),   -- NULL only for a rejected statement with a bad type
    term              TEXT,
    arm               TEXT,
    arm_in_quote      BOOLEAN,                                     -- FALSE = the group is named only in another sentence
    affected          INTEGER CHECK (affected >= 0),               -- participants affected (the model's count)
    denominator       INTEGER CHECK (denominator >= 0),
    percent           DOUBLE PRECISION,
    numbers           TEXT[],                                      -- the baseline's numbers, as written
    grade             TEXT,
    treatment_related BOOLEAN NOT NULL DEFAULT FALSE,
    cause             TEXT,
    all_cause         BOOLEAN NOT NULL DEFAULT FALSE,
    negated           BOOLEAN NOT NULL DEFAULT FALSE,
    sentence          INTEGER,
    quote_text        TEXT,
    quote_start       INTEGER,
    quote_end         INTEGER,
    rejected_code     TEXT,                                        -- NULL = passed every code check
    PRIMARY KEY (reading_id, position),
    CHECK ((quote_text IS NULL) = (quote_start IS NULL) AND (quote_start IS NULL) = (quote_end IS NULL))
);
