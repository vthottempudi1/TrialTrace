-- trialtrace/db/migrations/009_papers.sql
--
-- PURPOSE: Store papers read from saved PubMed files, and how each paper is tied to a trial.
--          A paper is identified by (pmid, raw_hash): the same paper in a newer download
--          is a new row, so the old file's reading is never overwritten.
--          raw_hash points to the untouched XML file, so every row traces to its source.
--          paper_links.reason is the plain-words result of the link check.
--          Not here: "this trial was searched and nothing matched" (a later lesson).

CREATE TABLE IF NOT EXISTS papers (
    pmid           TEXT        NOT NULL CHECK (pmid ~ '^[0-9]{1,9}$'),
    raw_hash       TEXT        NOT NULL CHECK (raw_hash ~ '^[0-9a-f]{64}$'),     -- hash of the saved XML file
    title          TEXT,                                                         -- NULL = not in the record
    abstract       TEXT,                                                         -- NULL = no abstract
    journal        TEXT,
    year           TEXT,                                                         -- raw text, e.g. '2020 Jan-Feb'
    listed_nct_ids TEXT[]      NOT NULL DEFAULT '{}',
    raw_file_path  TEXT        NOT NULL CHECK (raw_file_path <> ''),
    source_url     TEXT        NOT NULL,                                         -- the request address, never with the API key
    retrieved_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (pmid, raw_hash)
);

CREATE TABLE IF NOT EXISTS paper_links (
    nct_id         TEXT    NOT NULL CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    pmid           TEXT    NOT NULL,
    raw_hash       TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('listed', 'text_only', 'not_confirmed')),
    shared         BOOLEAN NOT NULL,
    other_nct_ids  TEXT[]  NOT NULL DEFAULT '{}',
    reason         TEXT    NOT NULL CHECK (reason <> ''),
    PRIMARY KEY (nct_id, pmid, raw_hash),                                        -- one verdict per trial and paper file
    FOREIGN KEY (pmid, raw_hash) REFERENCES papers (pmid, raw_hash)
);

CREATE INDEX IF NOT EXISTS paper_links_by_trial ON paper_links (nct_id);          -- "all papers for this trial"
