##############################################################################
# trialtrace/report.py
#
# ---------------------------------------------------------------------------
# PURPOSE
# ---------------------------------------------------------------------------
# This file turns the trials stored in our database into ONE readable report.
# It only READS the database (it never calls the registry). It is the "glue"
# that runs every check we built and puts the answers side by side:
#
#     check_results_posted   (checks/results_posted.py)   question 1
#     check_timeline         (checks/timeline.py)          question 2
#     track_record           (checks/track_record.py)      question 3, per sponsor
#     run_all patterns       (checks/patterns.py)          question 4, across trials
#     check_registry_changes (compare/registry_changes.py) question 5a
#     check_reported_outcomes (compare/reported_outcomes.py) question 5b, results arm
#     check_safety_registry  (checks/safety_registry.py)   question 6, registry side only: deaths, serious events (no comparison)
#     check_safety_summary   (compare/safety_rollup.py)    question 6: abstract safety numbers against the registry, one finding per quantity
#     check_paper_summary    (compare/paper_rollup.py)     question 5b, paper arm: one finding per registered outcome
#     check_paper_outcomes   (compare/paper_outcomes.py)   question 5b, paper arm: the per-paper detail (LLM + baseline)
#     save_run               (db/repository.py)            keeps the run as history
#
# Key rules this file follows:
#   - The report states what the stored records show. It never says a sponsor
#     or trial did something wrong: differences are "for review", and the words
#     "violation", "late" or "non-compliant" never appear.
#   - "Unknown" is shown as unknown. A trial we could not judge is listed with
#     the reason, not left out and not turned into "no".
#   - One trial can never cost you the whole report. If building one trial's
#     findings fails unexpectedly, that trial appears with an "unknown" entry
#     naming the error type, and the rest of the report is built as usual. A
#     failure of the database itself still stops the report.
#   - Every number about sponsors or patterns carries the label "in the
#     ingested corpus only": they describe the trials WE loaded, nothing wider.
#   - Every report is a new RUN. Its findings are saved so a person can review
#     them (review/review.py), and an earlier run is never changed.
#
# ---------------------------------------------------------------------------
# PIPELINE  (what build_report and save_and_render do, step by step)
# ---------------------------------------------------------------------------
# Step 1. List the stored trials                      (list_trial_ids)
#
# Step 2. For each trial, build a TrialReport         (build_trial_report)
#         - load its newest snapshot and that snapshot's evidence
#         - run question 1, question 2, question 5a and question 5b (results arm)
#         - the result is four Findings (one per check) plus one finding per registered
#           outcome per checked paper (5b paper arm), each with its own evidence IDs and limitations;
#           a trial never checked against papers gets one "unknown" finding
#
# Step 3. Add the corpus-wide parts
#         - track_record: results counts per sponsor (question 3)
#         - patterns: the named queries (question 4)
#
# Step 4. Save the run                                (save_run)
#         One row in runs plus one row per finding. This is what the review
#         step works on. Nothing here is ever overwritten.
#
# Step 5. Render to text                              (render_text)
#         A plain-text (Markdown) report, one section per trial, then the
#         sponsor and pattern sections.
#
# ---------------------------------------------------------------------------
# RESULT
# ---------------------------------------------------------------------------
#   Report       run_id, as_of, trials (list of TrialReport), sponsor_records, patterns
#   TrialReport  nct_id, title, sponsor, overall_status, findings
#   render_text  the Report as a string you can print or save to a file
#
# ---------------------------------------------------------------------------
# USE
# ---------------------------------------------------------------------------
#   report = build_report(conn, as_of=date.today(), run_id=new_run_id())
#   save_run(conn, report.run_id, report.as_of, report.all_findings())
#   conn.commit()                       # the caller commits
#   print(render_text(report))
##############################################################################

from datetime import date, datetime, timezone

import psycopg
from pydantic import BaseModel, Field

from trialtrace.checks.patterns import PatternResult, run_all
from trialtrace.checks.results_posted import check_results_posted
from trialtrace.safety.safety_registry import check_safety_registry
from trialtrace.checks.timeline import check_timeline
from trialtrace.checks.track_record import SponsorRecord, track_record
from trialtrace.compare.paper_outcomes import check_paper_outcomes
from trialtrace.compare.paper_rollup import check_paper_summary
from trialtrace.compare.registry_changes import check_registry_changes
from trialtrace.compare.reported_outcomes import check_reported_outcomes
from trialtrace.safety.safety_rollup import check_safety_summary
from trialtrace.contracts import Finding
from trialtrace.db.repository import get_evidence, get_latest_snapshot, get_safety, list_trial_ids


class TrialReport(BaseModel):
    """Everything the report says about one trial."""

    nct_id: str
    title: str
    sponsor: str | None
    overall_status: str
    findings: list[Finding]


class Report(BaseModel):
    """One whole report run."""

    run_id: str
    as_of: date                                         # the "today" the time-based check used
    trials: list[TrialReport] = Field(default_factory=list)
    sponsor_records: list[SponsorRecord] = Field(default_factory=list)
    patterns: list[PatternResult] = Field(default_factory=list)

    def all_findings(self) -> list[Finding]:
        """Every trial's findings in one list (what gets saved as the run)."""
        findings = []
        for trial in self.trials:
            findings += trial.findings
        return findings


def new_run_id() -> str:
    """A run ID that sorts by time, e.g. 'run-20261102T203015Z'."""
    return f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"


def build_trial_report(conn: psycopg.Connection, nct_id: str, as_of: date) -> TrialReport:
    """Run the per-trial checks (questions 1, 2, 5a and 5b results arm) on the newest stored version."""
    snapshot = get_latest_snapshot(conn, nct_id)
    if snapshot is None:
        raise ValueError(f"{nct_id} has no stored version")
    evidence = get_evidence(conn, nct_id, snapshot.content_hash)

    findings = [
        check_results_posted(nct_id, snapshot, evidence),
        check_timeline(nct_id, snapshot, evidence, as_of),
        check_registry_changes(conn, nct_id).to_finding(),
        check_reported_outcomes(conn, nct_id).to_finding(),
        check_safety_registry(nct_id, snapshot, get_safety(conn, nct_id, snapshot.content_hash), evidence),   # safety S4: registry numbers only
        *check_safety_summary(conn, nct_id),            # safety S8: abstract numbers against the registry, one finding per quantity
        *check_paper_summary(conn, nct_id),             # question 5b, paper arm: one finding per registered outcome (the review unit)
        *check_paper_outcomes(conn, nct_id),            # ... and one per outcome per paper (the detail), or one 'unknown' if never matched
    ]
    return TrialReport(
        nct_id=nct_id, title=snapshot.title, sponsor=snapshot.sponsor,
        overall_status=snapshot.overall_status, findings=findings,
    )


def failed_trial_report(nct_id: str, error: Exception) -> TrialReport:
    """What the report shows for a trial it could not build: an "unknown" finding saying so."""
    finding = Finding(
        nct_id=nct_id, check="report", category="unknown",
        claim="The report could not be built for this trial.",
        limitations=[f"Error: {type(error).__name__}. The trial is stored; try the report again after the cause is fixed."],
    )
    return TrialReport(nct_id=nct_id, title="not available", sponsor=None, overall_status="not available", findings=[finding])


def build_report(conn: psycopg.Connection, as_of: date, run_id: str) -> Report:
    """Build the whole report from the stored trials. Reads only; saves nothing.

    A problem with one trial is shown in the report as an "unknown" entry, so one trial can
    never cost you the whole report. A failure of the database itself still stops everything.
    """
    trials = []
    for nct_id in list_trial_ids(conn):
        try:
            trials.append(build_trial_report(conn, nct_id, as_of))
        except psycopg.Error:
            raise                                       # the database is failing: carrying on would be wrong
        except Exception as error:
            trials.append(failed_trial_report(nct_id, error))
    return Report(
        run_id=run_id, as_of=as_of, trials=trials,
        sponsor_records=track_record(conn), patterns=run_all(conn),
    )


def render_text(report: Report) -> str:
    """The report as plain Markdown text."""
    lines = [
        f"# TrialTrace report {report.run_id}",
        "",
        f"As of {report.as_of:%Y-%m-%d}. {len(report.trials)} trial(s), in the ingested corpus only. "
        "Differences are for review; nothing here is a conclusion about any sponsor.",
        "",
    ]

    # Summary: how many findings fall in each category
    counts: dict[str, int] = {}
    for finding in report.all_findings():
        counts[finding.category] = counts.get(finding.category, 0) + 1
    lines.append("## Summary")
    for category in sorted(counts):
        lines.append(f"- {category}: {counts[category]}")
    lines.append("")

    # One section per trial
    for trial in report.trials:
        lines.append(f"## {trial.nct_id}  {trial.title}")
        lines.append(f"Sponsor: {trial.sponsor or 'not given'}. Status: {trial.overall_status}.")
        for finding in trial.findings:
            lines.append(f"- [{finding.check}] {finding.category}")
            lines.append(f"  {finding.claim}")
            if finding.comparison_arm:
                lines.append(f"  Compared: {finding.comparison_arm}, matched {finding.match_method or 'method not given'}")
            if finding.evidence_ids:
                lines.append(f"  Evidence: {', '.join(finding.evidence_ids)}")
            for limitation in finding.limitations:
                lines.append(f"  Limitation: {limitation}")
        lines.append("")

    # Question 3
    lines.append("## Completed trials by sponsor (in the ingested corpus only)")
    if not report.sponsor_records:
        lines.append("- none")
    for record in report.sponsor_records:
        name = record.example_name or "sponsor not given"
        lines.append(
            f"- {name}: {record.completed_trials} completed; results shown {record.with_results}, "
            f"not shown {record.without_results}, unknown {record.unknown_results}"
        )
    lines.append("")

    # Question 4
    lines.append("## Patterns (in the ingested corpus only)")
    for pattern in report.patterns:
        lines.append(f"- {pattern.name}: {pattern.description}")
        if not pattern.rows:
            lines.append("  none")
        for row in pattern.rows:
            lines.append("  " + ", ".join(f"{key} {value}" for key, value in row.items()))
    lines.append("")

    return "\n".join(lines)
