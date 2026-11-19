##############################################################################
# trialtrace/review/review.py
#
# ---------------------------------------------------------------------------
# PURPOSE
# ---------------------------------------------------------------------------
# A person looks at the findings of a report run and records a decision about
# each one: APPROVE it, EDIT its wording, REJECT it with a reason, or say UNCLEAR
# (cannot be decided from the evidence, with a reason). Highest priority first.
# Decisions are saved as HISTORY. Nothing the system computed is changed: the
# finding stays as it was, and the review is stored beside it.
#
# It works with:
#
#     get_stored_findings   (db/repository.py)   the findings of one run, with their newest review
#     save_review           (db/repository.py)   adds one review to a finding's history
#
# Key rules this file follows:
#   - Reviews are history. Changing your mind adds a NEW review; the old one
#     stays. The current decision is the newest one.
#   - Who decided is always recorded (the reviewer's name is required).
#   - A rejection needs a reason, and an edit needs the new wording. Both are
#     checked here AND by the database.
#   - Edited wording follows the project's language rule: no verdict words such
#     as "violation", "fraud" or "late". A difference is described, not judged.
#   - A person's time goes to what needs judging. The default queue holds the
#     findings that report a DIFFERENCE or that could not be matched safely (and,
#     later, paper discrepancies). Plain facts read straight from the registry
#     (results shown or not, how long ago completion was) are left out unless
#     asked for with --all. Findings that claim nothing ("unknown", "no
#     change") are never in the queue.
#   - Nothing is learned from reviews automatically. The reasons are stored for
#     a person to read when improving the rules later.
#
# ---------------------------------------------------------------------------
# PIPELINE  (what review_run does, step by step)
# ---------------------------------------------------------------------------
# Step 1. Load the findings of the run                   (get_stored_findings)
#
# Step 2. Keep the ones that need a decision             (needs_review)
#         No review yet, a category that claims something, and (unless the
#         caller asks for everything) not a plain fact.
#
# Step 3. For each one, show it and ask what to do
#         - the check, the category, the claim, the evidence IDs, the limitations
#         - a = approve, e = edit, r = reject, u = unclear, s = skip, q = quit
#         - the queue is ordered by priority (see review_priority); the evidence text is shown, not only IDs
#
# Step 4. Record the decision                            (record_review)
#         - checks the rules above, then adds a row to the review history
#         - the caller's commit function runs after each decision, so a
#           crash or quit never loses decisions already made
#
# Step 5. Return how many of each decision were made
#
# ---------------------------------------------------------------------------
# RESULT
# ---------------------------------------------------------------------------
#   review_run returns a dict such as {"approved": 2, "edited": 1, "rejected": 0, "skipped": 3}
#
# ---------------------------------------------------------------------------
# USE
# ---------------------------------------------------------------------------
#   summary = review_run(conn, run_id, reviewer="Dr Rao", commit=conn.commit)
#   summary = review_run(conn, run_id, "Dr Rao", include_plain_facts=True)   # everything that claims something
#   record_review(conn, finding_id, "rejected", "Dr Rao", reason="Registry typo")
##############################################################################

import re
from typing import Callable

import psycopg

from trialtrace.compare.paper_outcomes import LOW_RISK_CATEGORIES
from trialtrace.compare.paper_rollup import SUMMARY_HIGH_RISK, SUMMARY_LOW_RISK
from trialtrace.safety.safety_rollup import SAFETY_HIGH_RISK, SAFETY_LOW_RISK
from trialtrace.contracts import StoredFinding
from trialtrace.db.repository import get_evidence_by_ids, get_stored_findings, save_review

DECISIONS = ("approved", "edited", "rejected", "unclear")

# Categories that claim nothing, so there is nothing for a person to decide (never in the queue)
NOTHING_TO_DECIDE = {"unknown", "no_registry_outcome_change", "reported_outcomes_match"}

# Plain facts read straight from the registry record: true or false, not a judgment call.
# They are in the queue only when the caller asks for everything.
PLAIN_FACTS = {
    "results_available", "results_not_in_snapshot",
    "over_12_months", "within_12_months", "estimated_date",
    "safety_registry_summary",                      # the registry's own adverse-event numbers, nothing compared
} | LOW_RISK_CATEGORIES | SUMMARY_LOW_RISK | SAFETY_LOW_RISK     # paper results where both systems agree: still provisional, but not first in the queue

# Words the project never uses to describe a trial; a difference is described, not judged
VERDICT_WORDS = re.compile(
    r"\b(violation|violations|violated|non-?compliant|fraud|fraudulent|illegal|unlawful|late|switching)\b",
    re.IGNORECASE,
)

CHOICES = {"a": "approved", "e": "edited", "r": "rejected", "u": "unclear"}

# Paper findings a person should look at first: the two systems disagree, the model could not match the
# outcome, or its answer could not be used. Everything else that needs a decision comes next; plain facts
# and paper results where both systems agree come last.
HIGH_PRIORITY = {"paper_check_failed", "paper_systems_disagree", "paper_outcome_unclear"} | SUMMARY_HIGH_RISK | SAFETY_HIGH_RISK

# The per-paper findings ("paper_outcome:<role>:<position>:<pmid>") are the supporting detail of the outcome-level
# summary findings. The default queue holds the summaries, so one outcome is judged once; the detail comes with --all.
DETAIL_PREFIX = "paper_outcome:"
PRIORITY_LABELS = {0: "high", 1: "normal", 2: "low"}
EVIDENCE_PREVIEW_CHARS = 300


def is_plain_fact(stored: StoredFinding) -> bool:
    """A plain fact needs no judgment. An agreed paper result that did not read every listed paper is not one."""
    finding = stored.finding
    if finding.category in (SUMMARY_LOW_RISK | SAFETY_LOW_RISK) and finding.evidence_completeness == "incomplete":
        return False
    return finding.category in PLAIN_FACTS


def review_priority(stored: StoredFinding) -> int:
    """0 = high, 1 = normal, 2 = low. The review queue is ordered by this number."""
    category = stored.finding.category
    if category == "unknown" and stored.finding.check.startswith(("safety_summary", "paper_outcome_summary")):
        return 0
    if category in HIGH_PRIORITY:
        return 0
    if is_plain_fact(stored):
        return 2
    return 1


def needs_review(stored: StoredFinding, include_plain_facts: bool = False) -> bool:
    """True if nobody has decided on this finding yet and it is worth a person's decision."""
    if stored.review_decision is not None:
        return False
    category = stored.finding.category
    if category in NOTHING_TO_DECIDE:
        return category == "unknown" and stored.finding.check.startswith(("safety_summary", "paper_outcome_summary"))
    if is_plain_fact(stored) and not include_plain_facts:
        return False
    if stored.finding.check.startswith(DETAIL_PREFIX) and not include_plain_facts:
        return False                                # per-paper detail: the outcome-level summary is the review unit
    return True


def record_review(
    conn: psycopg.Connection,
    finding_id: str,
    decision: str,
    reviewer: str,
    reason: str | None = None,
    edited_claim: str | None = None,
) -> None:
    """Check a decision against the rules, then add it to the finding's history."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    if not reviewer or not reviewer.strip():
        raise ValueError("the reviewer's name is required")
    if decision == "rejected" and not (reason and reason.strip()):
        raise ValueError("a rejection needs a reason")
    if decision == "unclear" and not (reason and reason.strip()):
        raise ValueError("an unclear answer needs a reason: what could not be decided from the evidence")
    if decision == "edited":
        if not (edited_claim and edited_claim.strip()):
            raise ValueError("an edit needs the new wording")
        found = VERDICT_WORDS.search(edited_claim)
        if found:
            raise ValueError(f"the edited wording must not use {found.group(0)!r}; describe the difference instead")
    save_review(conn, finding_id, decision, reviewer.strip(), reason, edited_claim)


def show_finding(
    stored: StoredFinding,
    position: int,
    total: int,
    show: Callable[[str], None],
    evidence: dict | None = None,
) -> None:
    """Print one finding the way a reviewer needs to read it: priority, claim, and the evidence text itself."""
    finding = stored.finding
    show("")
    show(f"[{position}/{total}] {finding.nct_id}  [{finding.check}]  {finding.category}  (priority: {PRIORITY_LABELS[review_priority(stored)]})")
    show(f"  {finding.claim}")
    for evidence_id in finding.evidence_ids:
        show(f"  Evidence: {evidence_id}")
        item = (evidence or {}).get(evidence_id)
        if item is not None:
            text = item.value if len(item.value) <= EVIDENCE_PREVIEW_CHARS else item.value[:EVIDENCE_PREVIEW_CHARS] + "..."
            show(f"    \"{text}\"")
    if finding.source_run_id:
        show(f"  Built from match run {finding.source_run_id}; the model's original answers are kept unchanged.")
    for limitation in finding.limitations:
        show(f"  Limitation: {limitation}")


def ask_until_given(question: str, ask: Callable[[str], str]) -> str:
    """Keep asking until the answer is not empty."""
    answer = ask(question).strip()
    while not answer:
        answer = ask(question).strip()
    return answer


def review_run(
    conn: psycopg.Connection,
    run_id: str,
    reviewer: str,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
    commit: Callable[[], None] = lambda: None,
    include_plain_facts: bool = False,
) -> dict[str, int]:
    """Go through the findings of a run that need a decision, one at a time."""
    queue = [s for s in get_stored_findings(conn, run_id) if needs_review(s, include_plain_facts)]
    queue.sort(key=review_priority)                 # high priority first; within a priority: trial, then check (stable sort)
    summary = {"approved": 0, "edited": 0, "rejected": 0, "unclear": 0, "skipped": 0}
    wanted = [evidence_id for s in queue for evidence_id in s.finding.evidence_ids]
    evidence = get_evidence_by_ids(conn, wanted)    # the evidence text, so the reviewer reads the quote, not just an ID
    show(f"{len(queue)} finding(s) need a decision in {run_id}, high priority first.")

    for position, stored in enumerate(queue, start=1):
        show_finding(stored, position, len(queue), show, evidence)

        choice = ""
        while choice not in ("a", "e", "r", "u", "s", "q"):
            choice = ask("  (a)pprove, (e)dit, (r)eject, (u)nclear, (s)kip, (q)uit > ").strip().lower()

        if choice == "q":
            break
        if choice == "s":
            summary["skipped"] += 1
            continue

        decision = CHOICES[choice]
        reason = None
        edited_claim = None
        if decision == "approved":
            reason = ask("  Note (optional) > ").strip() or None
        elif decision == "edited":
            edited_claim = ask_until_given("  New wording > ", ask)
            reason = ask("  Why (optional) > ").strip() or None
        elif decision == "unclear":
            reason = ask_until_given("  What could not be decided from the evidence? (required) > ", ask)
        else:
            reason = ask_until_given("  Reason (required) > ", ask)

        try:
            record_review(conn, stored.finding_id, decision, reviewer, reason, edited_claim)
        except ValueError as problem:               # for example, a verdict word in an edit
            show(f"  Not saved: {problem}")
            summary["skipped"] += 1
            continue
        commit()                                    # one decision at a time: quitting later loses nothing
        summary[decision] += 1

    show("")
    show("Done: " + ", ".join(f"{name} {count}" for name, count in summary.items()))
    return summary
