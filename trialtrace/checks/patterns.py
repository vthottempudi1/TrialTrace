##############################################################################
# trialtrace/checks/patterns.py
#
# PURPOSE: Question 4: patterns across trials, as a short list of NAMED SQL
#          queries. Each pattern is one fixed question over the newest
#          snapshot of every trial in our database. Results are lists of
#          trials or counts to look at, not verdicts, and carry the label
#          "in the ingested corpus only".
#          Adding a pattern = adding one entry to PATTERNS below.
#
# USE:     from trialtrace.checks.patterns import run_pattern, run_all
#          result = run_pattern(conn, "completed_with_estimated_date")
##############################################################################

from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel

SCOPE_LABEL = "in the ingested corpus only"


@dataclass(frozen=True)
class Pattern:
    name: str               # the key used to run it
    description: str        # one plain sentence for the report
    sql: str                # read-only SELECT; no user input is ever put into it


PATTERNS = {p.name: p for p in (
    Pattern(
        name="completed_with_estimated_date",
        description="Trials marked COMPLETED whose primary completion date is still labeled ESTIMATED.",
        sql="""
            SELECT nct_id
            FROM latest_snapshots
            WHERE overall_status = 'COMPLETED' AND primary_completion_type = 'ESTIMATED'
            ORDER BY nct_id
        """,
    ),
    Pattern(
        name="completed_missing_completion_date",
        description="Trials marked COMPLETED with no primary completion date or no date type.",
        sql="""
            SELECT nct_id
            FROM latest_snapshots
            WHERE overall_status = 'COMPLETED'
              AND (primary_completion_date IS NULL OR primary_completion_type IS NULL)
            ORDER BY nct_id
        """,
    ),
    Pattern(
        name="unknown_values_by_field",
        description="How many trials have no value for each field we rely on (a data-quality check).",
        sql="""
            SELECT COUNT(*)                                                AS trials,
                   COUNT(*) FILTER (WHERE sponsor IS NULL)                 AS unknown_sponsor,
                   COUNT(*) FILTER (WHERE has_results IS NULL)             AS unknown_has_results,
                   COUNT(*) FILTER (WHERE primary_completion_date IS NULL) AS unknown_completion_date,
                   COUNT(*) FILTER (WHERE primary_completion_type IS NULL) AS unknown_completion_type
            FROM latest_snapshots
        """,
    ),
)}


class PatternResult(BaseModel):
    """What one pattern query returned."""

    name: str
    description: str
    rows: list[dict]                    # e.g. [{"nct_id": "NCT..."}] or one row of counts
    scope: str = SCOPE_LABEL


def run_pattern(conn: psycopg.Connection, name: str) -> PatternResult:
    """Run one named pattern. An unknown name is an error, never an empty result."""
    if name not in PATTERNS:
        raise ValueError(f"unknown pattern {name!r}; choose from {sorted(PATTERNS)}")
    pattern = PATTERNS[name]
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(pattern.sql)
        rows = cur.fetchall()
    return PatternResult(name=pattern.name, description=pattern.description, rows=rows)


def run_all(conn: psycopg.Connection) -> list[PatternResult]:
    """Every pattern, in the order listed above."""
    return [run_pattern(conn, name) for name in PATTERNS]
