##############################################################################
# trialtrace/review/link_review.py
#
# PURPOSE: A person says how each stored paper relates to a trial:
#          reports_results / describes_protocol / mentions_only / not_this_trial.
#          The code's link check (listed / text_only / not_confirmed) is shown but never replaced.
#          Answers are insert-only history; the newest is current. Excluding a paper needs a reason.
#
# USE:     summary = review_links(conn, "NCT04644237", reviewer="Dr Rao", commit=conn.commit)
#          record_link_review(conn, "NCT04644237", pmid, raw_hash, "mentions_only", "Dr Rao")
##############################################################################

from typing import Callable

import psycopg

from trialtrace.db.repository import get_link_reviews, get_papers_for_trial, save_link_review

RELATIONSHIPS = ("reports_results", "describes_protocol", "mentions_only", "not_this_trial")
CHOICES = {"r": "reports_results", "p": "describes_protocol", "m": "mentions_only", "n": "not_this_trial"}
TITLE_PREVIEW_CHARS = 150


def record_link_review(
    conn: psycopg.Connection,
    nct_id: str,
    pmid: str,
    raw_hash: str,
    relationship: str,
    reviewer: str,
    reason: str | None = None,
) -> None:
    """Check an answer against the rules, then add it to the paper's history."""
    if relationship not in RELATIONSHIPS:
        raise ValueError(f"relationship must be one of {RELATIONSHIPS}, got {relationship!r}")
    if not reviewer or not reviewer.strip():
        raise ValueError("the reviewer's name is required")
    if relationship == "not_this_trial" and not (reason and reason.strip()):
        raise ValueError("saying a paper is not about this trial needs a reason")
    save_link_review(conn, nct_id, pmid, raw_hash, relationship, reviewer.strip(), reason)


def review_links(
    conn: psycopg.Connection,
    nct_id: str,
    reviewer: str,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
    commit: Callable[[], None] = lambda: None,
) -> dict[str, int]:
    """Go through the papers of one trial that have no answer yet, one at a time."""
    papers = get_papers_for_trial(conn, nct_id)
    answered = get_link_reviews(conn, nct_id)
    queue = [paper for paper in papers if (paper["pmid"], paper["raw_hash"]) not in answered]
    summary = {name: 0 for name in RELATIONSHIPS}
    summary["skipped"] = 0
    show(f"{len(queue)} paper(s) of {nct_id} need a relationship answer.")

    for position, paper in enumerate(queue, start=1):
        title = paper["title"] or "(no title)"
        if len(title) > TITLE_PREVIEW_CHARS:
            title = title[:TITLE_PREVIEW_CHARS] + "..."
        show("")
        show(f"[{position}/{len(queue)}] PMID {paper['pmid']}  code link check: {paper['status']}")
        show(f"  {title}")
        show(f"  Reason: {paper['reason']}")

        choice = ""
        while choice not in ("r", "p", "m", "n", "s", "q"):
            choice = ask("  (r)eports results, (p)rotocol, (m)entions only, (n)ot this trial, (s)kip, (q)uit > ").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            summary["skipped"] += 1
            continue

        relationship = CHOICES[choice]
        if relationship == "not_this_trial":
            reason = ""
            while not reason:
                reason = ask("  Reason (required) > ").strip()
        else:
            reason = ask("  Note (optional) > ").strip() or None

        record_link_review(conn, nct_id, paper["pmid"], paper["raw_hash"], relationship, reviewer, reason)
        commit()                                    # one answer at a time: quitting later loses nothing
        summary[relationship] += 1

    show("")
    show("Done: " + ", ".join(f"{name} {count}" for name, count in summary.items()))
    return summary
