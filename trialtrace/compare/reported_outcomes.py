##############################################################################
# trialtrace/compare/reported_outcomes.py
#
# PURPOSE: Question 5b, results arm: do the outcomes a trial REPORTED in its posted
#          results match the outcomes it REGISTERED? Plain code, no LLM: both sides are
#          structured records from the registry.
#          Same title (capitals and punctuation ignored) = same outcome. Then role and
#          time frame are compared. Descriptions are NOT compared (results descriptions
#          are often longer than registered ones, so it would only add noise).
#          Kept apart, like 5a:
#            differences  = clear (registered but not reported, reported but not registered,
#                           role differs, time frame differs)
#            needs_review = could not be matched safely (never called a difference)
#          "No results posted" and "not extracted" are never "no differences".
#
# PIPELINE (what compare_registered_to_reported does):
#   1. why_not_comparable -> stop with "unknown" / "no_results_posted"
#   2. group both lists by title key
#   3. per title: duplicate -> needs_review; one side only -> registered_only / reported_only;
#      both -> compare_pair (role, time frame)
#   4. per role: a registered_only AND a reported_only of the same role may be one outcome
#      reworded -> needs_review; a reported outcome with an unreadable type -> needs_review
#   5. build the ReportedComparison -> .to_finding()
#
# LIMIT: the registered list is the one in the SAME saved version, which may have been edited
#        after results were posted. This is not a comparison with the original plan.
#
# FROM THE DATABASE: check_reported_outcomes(conn, nct_id) loads the newest saved version, its
#   registered outcomes (get_outcomes) and its reported outcomes (get_results_outcomes), then compares.
#
# USE:     report = check_reported_outcomes(conn, "NCT04644237")                      # from the database
#          report = compare_registered_to_reported(snapshot, registered, reported)    # from lists you build
#          finding = report.to_finding()
##############################################################################

from typing import Literal

import psycopg
from pydantic import BaseModel, Field

from trialtrace.compare.registry_changes import ROLES, clean, evidence_ids, title_key
from trialtrace.contracts import Finding, OutcomeExtraction, RegisteredOutcome, ResultsExtraction, ResultsOutcome, Snapshot
from trialtrace.db.repository import get_outcomes, get_recent_versions, get_results_outcomes
from trialtrace.parse.results_parser import results_evidence_ids


class ReportedChange(BaseModel):
    """One difference, or one item that needs review."""

    kind: Literal["registered_only", "reported_only", "role_differs", "time_frame_differs", "ambiguous", "type_unreadable"]
    measure_text: str                               # the outcome's text
    before: str | None = None                       # the registered value (for registered_only: the role)
    after: str | None = None                        # the reported value (for reported_only: the type)
    evidence_ids: list[str] = Field(default_factory=list)


class ReportedComparison(BaseModel):
    nct_id: str
    status: Literal["differences", "needs_review", "no_differences", "no_results_posted", "unknown"]
    reason: str | None = None                       # why the status is unknown / no_results_posted
    captured_at: str | None = None                  # when WE downloaded the compared version (date text)
    registry_update: str | None = None              # the registry's own date for that version
    differences: list[ReportedChange] = Field(default_factory=list)
    needs_review: list[ReportedChange] = Field(default_factory=list)
    compared_evidence_ids: list[str] = Field(default_factory=list)

    def to_finding(self) -> Finding:
        """The comparison as one Finding: a plain sentence plus the evidence it rests on."""
        limits = [
            "The registered outcomes are those in the same saved version of the record, which may have been edited "
            "after results were posted; this is not a comparison with the original plan.",
            "Descriptions are not compared.",
        ]
        arm = {"check": "reported_outcomes", "comparison_arm": "results_submission", "match_method": "deterministic"}

        if self.status in ("unknown", "no_results_posted"):
            return Finding(
                nct_id=self.nct_id, category="unknown", **arm,
                claim="Whether the reported outcomes match the registered outcomes is unknown.",
                limitations=[self.reason or "Not enough information.", *limits],
            )

        version = f"the version captured {self.captured_at} (registry update {self.registry_update or 'not given'})"
        if self.status == "no_differences":
            return Finding(
                nct_id=self.nct_id, category="reported_outcomes_match", **arm,
                claim=f"The outcomes in the posted results match the registered outcomes in {version}.",
                evidence_ids=self.compared_evidence_ids, limitations=limits,
            )

        evidence = []
        review_text = ""
        for item in self.needs_review:
            evidence += item.evidence_ids
        if self.needs_review:
            review_text = ("could not be matched safely: " + "; ".join(describe(item) for item in self.needs_review)
                           + ". They may be the same outcomes worded differently")

        if self.status == "needs_review":
            return Finding(
                nct_id=self.nct_id, category="reported_outcomes_need_review", **arm,
                claim=f"Some outcomes in {version} {review_text}, so no difference is confirmed.",
                evidence_ids=sorted(set(evidence)), limitations=[*limits, "Needs review."],
            )

        phrases = []
        for change in self.differences:
            phrases.append(describe(change))
            evidence += change.evidence_ids
        claim = f"{len(self.differences)} difference(s) between registered and reported outcomes in {version}: " + "; ".join(phrases)
        if review_text:
            claim += f". Separately, some outcomes {review_text}"
        return Finding(
            nct_id=self.nct_id, category="reported_outcome_differences", **arm,
            claim=claim + ".", evidence_ids=sorted(set(evidence)), limitations=[*limits, "Differences are for review."],
        )


def describe(change: ReportedChange) -> str:
    """One short phrase for one item."""
    title = f'"{change.measure_text}"'
    if change.kind == "registered_only":
        return f"{title} is registered as {change.before} but no outcome with that title is in the posted results"
    if change.kind == "reported_only":
        return f"{title} is in the posted results (listed as {change.after}) but not in the registered outcomes"
    if change.kind == "role_differs":
        return f"{title} is registered as {change.before} but listed as {change.after} in the posted results"
    if change.kind == "time_frame_differs":
        return f'{title} has time frame "{change.before}" in the registered outcomes and "{change.after}" in the posted results'
    if change.kind == "type_unreadable":
        return f"{title} is in the posted results with a type we could not read"
    return f"outcomes titled {title} appear more than once, so they could not be matched"


def group_by_title(outcomes: list) -> dict[str, list]:
    """{title key: [outcomes with that title]}. Usually one each; two means we cannot match safely."""
    groups: dict[str, list] = {}
    for outcome in outcomes:
        groups.setdefault(title_key(outcome.measure_text), []).append(outcome)
    return groups


def compare_pair(registered: RegisteredOutcome, reported: ResultsOutcome) -> list[ReportedChange]:
    """Differences between the same outcome as registered and as reported."""
    changes = []
    ids = evidence_ids(registered) + results_evidence_ids(reported)

    if reported.result_type is None:
        changes.append(ReportedChange(kind="type_unreadable", measure_text=reported.measure_text, evidence_ids=ids))
    elif reported.result_type != registered.role:
        changes.append(ReportedChange(kind="role_differs", measure_text=reported.measure_text,
                                      before=registered.role, after=reported.result_type, evidence_ids=ids))
    if clean(registered.time_frame) != clean(reported.time_frame):
        changes.append(ReportedChange(kind="time_frame_differs", measure_text=reported.measure_text,
                                      before=registered.time_frame, after=reported.time_frame, evidence_ids=ids))
    return changes


def why_not_comparable(registered: OutcomeExtraction | None, reported: ResultsExtraction | None) -> tuple[str, str] | None:
    """None if the two lists can be compared; otherwise (status, reason)."""
    if registered is None:
        return "unknown", "Registered outcomes were not extracted for this version."
    if reported is None:
        return "unknown", "Outcomes from the posted results were not extracted for this version."
    if registered.status == "absent":
        return "unknown", "This version has no registered outcomes section."
    if reported.status == "absent":
        return "no_results_posted", "No results outcome measures are posted for this version, so there is nothing to compare."
    return None


def compare_registered_to_reported(
    snapshot: Snapshot,
    registered: OutcomeExtraction | None,
    reported: ResultsExtraction | None,
    captured_at: str | None = None,
) -> ReportedComparison:
    """Compare the registered and reported outcomes of ONE saved version of a trial."""
    nct_id = snapshot.nct_id
    stamp = dict(captured_at=captured_at, registry_update=snapshot.registry_last_update)

    stop = why_not_comparable(registered, reported)
    if stop is not None:
        return ReportedComparison(nct_id=nct_id, status=stop[0], reason=stop[1], **stamp)

    # why_not_comparable already guaranteed this; the line also tells the editor's type checker
    assert registered is not None and reported is not None

    registered_groups = group_by_title(registered.outcomes)
    reported_groups = group_by_title(reported.outcomes)

    titles = list(registered_groups)
    for key in reported_groups:
        if key not in registered_groups:
            titles.append(key)

    differences, needs_review, registered_only, reported_only = [], [], [], []
    for key in titles:
        reg_list = registered_groups.get(key, [])
        rep_list = reported_groups.get(key, [])

        if len(reg_list) > 1 or len(rep_list) > 1:
            ids = [i for o in reg_list for i in evidence_ids(o)] + [i for o in rep_list for i in results_evidence_ids(o)]
            needs_review.append(ReportedChange(kind="ambiguous", measure_text=(reg_list + rep_list)[0].measure_text, evidence_ids=ids))
        elif not rep_list:
            registered_only.append(ReportedChange(kind="registered_only", measure_text=reg_list[0].measure_text,
                                                  before=reg_list[0].role, evidence_ids=evidence_ids(reg_list[0])))
        elif not reg_list:
            item = ReportedChange(kind="reported_only", measure_text=rep_list[0].measure_text,
                                  after=rep_list[0].result_type, evidence_ids=results_evidence_ids(rep_list[0]))
            if rep_list[0].result_type is None:             # an unreadable type: never guess a role
                item.kind = "type_unreadable"
                needs_review.append(item)
            else:
                reported_only.append(item)
        else:
            pair = compare_pair(reg_list[0], rep_list[0])
            needs_review += [c for c in pair if c.kind == "type_unreadable"]
            differences += [c for c in pair if c.kind != "type_unreadable"]

    # In one role, a registered outcome with no reported match next to a reported outcome with no
    # registered match may be one outcome reworded: set both aside. Otherwise they are clear differences.
    for role in ROLES:
        registered_here = [c for c in registered_only if c.before == role]
        reported_here = [c for c in reported_only if c.after == role]
        if registered_here and reported_here:
            needs_review += registered_here + reported_here
        else:
            differences += registered_here + reported_here
    differences += [c for c in reported_only if c.after not in ROLES]       # post hoc: not in the plan by definition

    compared = [i for o in registered.outcomes for i in evidence_ids(o)] + [i for o in reported.outcomes for i in results_evidence_ids(o)]

    if differences:
        status, reason = "differences", None
    elif needs_review:
        status, reason = "needs_review", None
    elif compared:
        status, reason = "no_differences", None
    else:
        status, reason = "unknown", "Neither list has any outcomes."

    return ReportedComparison(
        nct_id=nct_id, status=status, reason=reason, differences=differences,
        needs_review=needs_review, compared_evidence_ids=compared, **stamp,
    )


def check_reported_outcomes(conn: psycopg.Connection, nct_id: str) -> ReportedComparison:
    """Question 5b (results arm) for one stored trial: its newest saved version, registered vs reported."""
    recent = get_recent_versions(conn, nct_id, limit=1)         # the newest version and when WE last saw it
    if not recent:
        return ReportedComparison(nct_id=nct_id, status="unknown", reason="This trial is not stored.")

    snapshot, last_seen = recent[0]
    registered = get_outcomes(conn, nct_id, snapshot.content_hash)
    reported = get_results_outcomes(conn, nct_id, snapshot.content_hash)
    return compare_registered_to_reported(snapshot, registered, reported, captured_at=f"{last_seen:%Y-%m-%d}")
