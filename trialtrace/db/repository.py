##############################################################################
# trialtrace/db/repository.py
#
# PURPOSE: All SQL for saving and reading snapshots lives here, so no other
#          file contains SQL. The caller owns the connection. save_snapshot
#          is all-or-nothing: if the caller has no transaction open, it commits
#          when done; inside the caller's transaction, the caller commits.
#
# USE:     with psycopg.connect(url) as conn:
#              is_new = save_snapshot(conn, snapshot, evidence)
#              latest = get_latest_snapshot(conn, "NCT04644237")
##############################################################################

from datetime import date, datetime, timezone

import psycopg
from psycopg.rows import dict_row                   # rows as dicts: {"nct_id": ...}

from trialtrace.checks.paper_link import PaperLink
from trialtrace.contracts import (
    Evidence, Finding, OutcomeExtraction, RegisteredOutcome, ResultsExtraction, ResultsOutcome, SafetyArm,
    SafetyEvent, SafetyEventCount, SafetyExtraction, Snapshot, StoredFinding,
)
from trialtrace.parse.pubmed_parser import Paper
from trialtrace.parse.sponsor import normalize_sponsor
from trialtrace.sources.pubmed import PubMedSearch


def as_utc(row: dict) -> dict:
    """The row with its capture times in UTC. The database shows times in its own session time zone,
    which would print a different calendar date in a finding than the saved file's UTC time."""
    for key in ("retrieved_at", "last_seen"):
        if row.get(key) is not None:
            row[key] = row[key].astimezone(timezone.utc)
    return row


def save_snapshot(
    conn: psycopg.Connection,
    snapshot: Snapshot,
    evidence: list[Evidence],
    outcomes: OutcomeExtraction | None = None,
    results: ResultsExtraction | None = None,
    safety: SafetyExtraction | None = None,
) -> bool:
    """Save a snapshot, its evidence, its registered outcomes and its reported (results) outcomes together.
    Returns True if the content was new.

    Same content seen again adds no snapshot row, only a new observation, so the
    full sequence of sightings is kept (A -> B -> A) and latest_snapshots follows it.
    outcomes=None means outcomes were not extracted (stored as unknown, never as "none");
    a later call with the same content can fill them in, but never overwrites them.
    results works the same way for the outcomes reported in posted results, and safety for the adverse-event module.
    """
    row = snapshot.model_dump()
    row["source_url"] = str(snapshot.source_url)       # the database wants plain text, not a URL object
    row["sponsor_normalized"] = normalize_sponsor(snapshot.sponsor)

    with conn.transaction():                        # snapshot and evidence: both saved or neither
        existed = conn.execute(
            "SELECT 1 FROM snapshots WHERE nct_id = %(nct_id)s AND content_hash = %(content_hash)s",
            row,
        ).fetchone() is not None

        conn.execute(
            """
            INSERT INTO snapshots (
                nct_id, content_hash, title, sponsor, sponsor_normalized, overall_status,
                primary_completion_date, primary_completion_type, has_results,
                source_url, raw_file_path, retrieved_at, registry_last_update
            ) VALUES (
                %(nct_id)s, %(content_hash)s, %(title)s, %(sponsor)s, %(sponsor_normalized)s, %(overall_status)s,
                %(primary_completion_date)s, %(primary_completion_type)s, %(has_results)s,
                %(source_url)s, %(raw_file_path)s, %(retrieved_at)s, %(registry_last_update)s
            )
            ON CONFLICT (nct_id, content_hash) DO NOTHING
            """,
            row,
        )

        conn.execute(
            """
            INSERT INTO observations (
                nct_id, content_hash, observed_at
            ) VALUES (
                %(nct_id)s, %(content_hash)s, %(retrieved_at)s
            )
            ON CONFLICT DO NOTHING
            """,
            row,
        )

        if outcomes is not None:
            # Fill in the status only if it is still unknown; never overwrite a stored answer
            conn.execute(
                "UPDATE snapshots SET outcomes_status = %(status)s "
                "WHERE nct_id = %(nct_id)s AND content_hash = %(content_hash)s AND outcomes_status IS NULL",
                {"status": outcomes.status, "nct_id": snapshot.nct_id, "content_hash": snapshot.content_hash},
            )
            for outcome in outcomes.outcomes:
                conn.execute(
                    """
                    INSERT INTO registered_outcomes (
                        nct_id, content_hash, role, position, measure_text, description, time_frame
                    ) VALUES (
                        %(nct_id)s, %(content_hash)s, %(role)s, %(position)s, %(measure_text)s, %(description)s, %(time_frame)s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    outcome.model_dump(),
                )

        if results is not None:
            conn.execute(
                "UPDATE snapshots SET results_status = %(status)s "
                "WHERE nct_id = %(nct_id)s AND content_hash = %(content_hash)s AND results_status IS NULL",
                {"status": results.status, "nct_id": snapshot.nct_id, "content_hash": snapshot.content_hash},
            )
            for reported in results.outcomes:
                conn.execute(
                    """
                    INSERT INTO results_outcomes (
                        nct_id, content_hash, position, result_type, measure_text,
                        description, time_frame, population, reporting_status
                    ) VALUES (
                        %(nct_id)s, %(content_hash)s, %(position)s, %(result_type)s, %(measure_text)s,
                        %(description)s, %(time_frame)s, %(population)s, %(reporting_status)s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    reported.model_dump(),
                )

        if safety is not None:
            save_safety_rows(conn, snapshot.nct_id, snapshot.content_hash, safety)

        save_evidence(conn, evidence)

    return not existed


def save_evidence(conn: psycopg.Connection, evidence: list[Evidence]) -> int:
    """Save evidence rows (registry values, abstract quotes). The same evidence ID saved twice adds nothing.
    Returns how many were new."""
    added = 0
    for item in evidence:
        item_row = item.model_dump()
        item_row["source_url"] = str(item.source_url)
        result = conn.execute(
            """
            INSERT INTO evidence (
                evidence_id, nct_id, content_hash, source, source_url,
                field_path, value, retrieved_at
            ) VALUES (
                %(evidence_id)s, %(nct_id)s, %(content_hash)s, %(source)s, %(source_url)s,
                %(field_path)s, %(value)s, %(retrieved_at)s
            )
            ON CONFLICT (evidence_id) DO NOTHING
            """,
            item_row,
        )
        added += result.rowcount
    return added


def list_trial_ids(conn: psycopg.Connection) -> list[str]:
    """Every trial that has at least one stored version, in ID order."""
    rows = conn.execute("SELECT nct_id FROM latest_snapshots ORDER BY nct_id").fetchall()
    return [row[0] for row in rows]


def make_finding_id(run_id: str, finding: Finding) -> str:
    """The ID of a finding inside a run: '<run_id>:<nct_id>:<check>'."""
    return f"{run_id}:{finding.nct_id}:{finding.check}"


def save_run(conn: psycopg.Connection, run_id: str, as_of: date, findings: list[Finding]) -> None:
    """Save one report run and all its findings together (both saved, or neither)."""
    with conn.transaction():
        conn.execute("INSERT INTO runs (run_id, as_of) VALUES (%s, %s)", (run_id, as_of))
        for finding in findings:
            conn.execute(
                """
                INSERT INTO findings (finding_id, run_id, nct_id, check_name, category, claim, evidence_ids, limitations,
                                      comparison_arm, match_method, source_run_id, evidence_completeness)
                VALUES (%(finding_id)s, %(run_id)s, %(nct_id)s, %(check_name)s, %(category)s, %(claim)s,
                        %(evidence_ids)s, %(limitations)s, %(comparison_arm)s, %(match_method)s, %(source_run_id)s, %(evidence_completeness)s)
                """,
                {
                    "finding_id": make_finding_id(run_id, finding), "run_id": run_id, "nct_id": finding.nct_id,
                    "check_name": finding.check, "category": finding.category, "claim": finding.claim,
                    "evidence_ids": finding.evidence_ids, "limitations": finding.limitations,
                    "comparison_arm": finding.comparison_arm, "match_method": finding.match_method,
                    "source_run_id": finding.source_run_id, "evidence_completeness": finding.evidence_completeness,
                },
            )


def get_latest_run_id(conn: psycopg.Connection) -> str | None:
    """The most recently created run, or None if no report was ever generated."""
    row = conn.execute("SELECT run_id FROM runs ORDER BY created_at DESC, run_id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def get_stored_findings(conn: psycopg.Connection, run_id: str) -> list[StoredFinding]:
    """The findings of one run, each with its newest review (if any), in trial then check order."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT f.finding_id, f.run_id, f.nct_id, f.check_name, f.category, f.claim,
                   f.evidence_ids, f.limitations, f.comparison_arm, f.match_method, f.source_run_id, f.evidence_completeness,
                   r.decision, r.reviewer, r.reason, r.edited_claim
            FROM findings f
            LEFT JOIN current_reviews r ON r.finding_id = f.finding_id
            WHERE f.run_id = %(run_id)s
            ORDER BY f.nct_id, f.check_name
            """,
            {"run_id": run_id},
        )
        rows = cur.fetchall()

    stored = []
    for row in rows:
        finding = Finding(
            nct_id=row["nct_id"], check=row["check_name"], category=row["category"], claim=row["claim"],
            evidence_ids=row["evidence_ids"], limitations=row["limitations"],
            comparison_arm=row["comparison_arm"], match_method=row["match_method"], source_run_id=row["source_run_id"],
            evidence_completeness=row["evidence_completeness"],
        )
        stored.append(StoredFinding(
            finding_id=row["finding_id"], run_id=row["run_id"], finding=finding,
            review_decision=row["decision"], reviewer=row["reviewer"],
            review_reason=row["reason"], edited_claim=row["edited_claim"],
        ))
    return stored


def save_review(
    conn: psycopg.Connection,
    finding_id: str,
    decision: str,
    reviewer: str,
    reason: str | None = None,
    edited_claim: str | None = None,
) -> None:
    """Add one review to a finding's history. Never changes an earlier review."""
    conn.execute(
        "INSERT INTO reviews (finding_id, decision, reviewer, reason, edited_claim) VALUES (%s, %s, %s, %s, %s)",
        (finding_id, decision, reviewer, reason, edited_claim),
    )


def get_review_history(conn: psycopg.Connection, finding_id: str) -> list[dict]:
    """Every review of one finding, oldest first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT decision, reviewer, reason, edited_claim, reviewed_at FROM reviews "
            "WHERE finding_id = %s ORDER BY reviewed_at, id",
            (finding_id,),
        )
        return cur.fetchall()


def get_evidence(conn: psycopg.Connection, nct_id: str, content_hash: str) -> list[Evidence]:
    """All evidence rows for one saved version of a trial (what the checks cite)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM evidence WHERE nct_id = %s AND content_hash = %s ORDER BY evidence_id",
            (nct_id, content_hash),
        )
        rows = cur.fetchall()
    return [Evidence(**row) for row in rows]


def recompute_sponsor_keys(conn: psycopg.Connection) -> int:
    """Recompute sponsor_normalized for every stored snapshot, after the naming rules change.

    Only rows whose key actually changes are updated. Returns how many changed.
    """
    rows = conn.execute("SELECT nct_id, content_hash, sponsor FROM snapshots").fetchall()
    changed = 0
    for nct_id, content_hash, sponsor in rows:
        key = normalize_sponsor(sponsor)
        result = conn.execute(
            "UPDATE snapshots SET sponsor_normalized = %s "
            "WHERE nct_id = %s AND content_hash = %s AND sponsor_normalized IS DISTINCT FROM %s",
            (key, nct_id, content_hash, key),
        )
        changed += result.rowcount
    return changed


def get_recent_versions(conn: psycopg.Connection, nct_id: str, limit: int = 2) -> list[tuple[Snapshot, datetime]]:
    """The most recently seen DIFFERENT versions of one trial, newest first.

    Each item is (snapshot, the last time we saw that version). A -> B -> A gives A first, then B.
    An unknown trial gives an empty list.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT s.*, MAX(o.observed_at) AS last_seen
            FROM observations o
            JOIN snapshots s ON s.nct_id = o.nct_id AND s.content_hash = o.content_hash
            WHERE o.nct_id = %(nct_id)s
            GROUP BY s.nct_id, s.content_hash
            ORDER BY last_seen DESC
            LIMIT %(limit)s
            """,
            {"nct_id": nct_id, "limit": limit},
        )
        rows = cur.fetchall()
    rows = [as_utc(row) for row in rows]
    return [(Snapshot(**row), row["last_seen"]) for row in rows]


def get_outcomes(conn: psycopg.Connection, nct_id: str, content_hash: str) -> OutcomeExtraction | None:
    """The registered outcomes of one saved version: primary, then secondary, then other,
    each in the registry's own order.

    None = outcomes were never extracted for this version (unknown). That differs from
    status "ok" with no outcomes (section empty) and from status "absent" (no section).
    """
    status = conn.execute(
        "SELECT outcomes_status FROM snapshots WHERE nct_id = %s AND content_hash = %s",
        (nct_id, content_hash),
    ).fetchone()
    if status is None or status[0] is None:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT nct_id, content_hash, role, position, measure_text, description, time_frame "
            "FROM registered_outcomes WHERE nct_id = %s AND content_hash = %s "
            "ORDER BY CASE role WHEN 'primary' THEN 0 WHEN 'secondary' THEN 1 ELSE 2 END, position",
            (nct_id, content_hash),
        )
        rows = cur.fetchall()
    return OutcomeExtraction(status=status[0], outcomes=[RegisteredOutcome(**row) for row in rows])


def get_results_outcomes(conn: psycopg.Connection, nct_id: str, content_hash: str) -> ResultsExtraction | None:
    """The outcomes one saved version REPORTED in its posted results, in the registry's own order.

    None = never extracted for this version (unknown). That differs from status "ok" with no
    outcomes (empty section) and from status "absent" (no results measures posted).
    """
    status = conn.execute(
        "SELECT results_status FROM snapshots WHERE nct_id = %s AND content_hash = %s",
        (nct_id, content_hash),
    ).fetchone()
    if status is None or status[0] is None:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT nct_id, content_hash, position, result_type, measure_text, description, "
            "time_frame, population, reporting_status "
            "FROM results_outcomes WHERE nct_id = %s AND content_hash = %s ORDER BY position",
            (nct_id, content_hash),
        )
        rows = cur.fetchall()
    return ResultsExtraction(status=status[0], outcomes=[ResultsOutcome(**row) for row in rows])


def save_safety_rows(conn: psycopg.Connection, nct_id: str, content_hash: str, safety: SafetyExtraction) -> None:
    """Fill in the adverse-event module of one saved version. Never overwrites: the status is set only while unknown."""
    key = {"nct_id": nct_id, "content_hash": content_hash}
    conn.execute(
        "UPDATE snapshots SET safety_status = %(status)s "
        "WHERE nct_id = %(nct_id)s AND content_hash = %(content_hash)s AND safety_status IS NULL",
        {"status": safety.status, **key},
    )
    if safety.status == "absent":
        return
    conn.execute(
        "INSERT INTO safety_module (nct_id, content_hash, frequency_threshold, time_frame, description) "
        "VALUES (%(nct_id)s, %(content_hash)s, %(frequency_threshold)s, %(time_frame)s, %(description)s) "
        "ON CONFLICT DO NOTHING",
        {**key, "frequency_threshold": safety.frequency_threshold, "time_frame": safety.time_frame,
         "description": safety.description},
    )
    for arm in safety.arms:
        conn.execute(
            """
            INSERT INTO safety_arms (
                nct_id, content_hash, position, group_id, title, description, deaths_affected, deaths_at_risk,
                serious_affected, serious_at_risk, other_affected, other_at_risk, looks_pooled
            ) VALUES (
                %(nct_id)s, %(content_hash)s, %(position)s, %(group_id)s, %(title)s, %(description)s,
                %(deaths_affected)s, %(deaths_at_risk)s, %(serious_affected)s, %(serious_at_risk)s,
                %(other_affected)s, %(other_at_risk)s, %(looks_pooled)s
            ) ON CONFLICT DO NOTHING
            """,
            arm.model_dump(),
        )
    for event in safety.events:
        conn.execute(
            """
            INSERT INTO safety_events (
                nct_id, content_hash, kind, position, term, notes, organ_system, vocabulary, assessment_type
            ) VALUES (
                %(nct_id)s, %(content_hash)s, %(kind)s, %(position)s, %(term)s, %(notes)s, %(organ_system)s,
                %(vocabulary)s, %(assessment_type)s
            ) ON CONFLICT DO NOTHING
            """,
            event.model_dump(exclude={"counts"}),
        )
        for order, count in enumerate(event.counts):
            conn.execute(
                """
                INSERT INTO safety_event_counts (
                    nct_id, content_hash, kind, event_position, position, group_id, affected, at_risk, events
                ) VALUES (
                    %(nct_id)s, %(content_hash)s, %(kind)s, %(event_position)s, %(order)s, %(group_id)s,
                    %(affected)s, %(at_risk)s, %(events)s
                ) ON CONFLICT DO NOTHING
                """,
                {**key, "kind": event.kind, "event_position": event.position, "order": order, **count.model_dump()},
            )


def get_safety(conn: psycopg.Connection, nct_id: str, content_hash: str) -> SafetyExtraction | None:
    """The adverse-event module of one saved version, in the registry's own order.

    None = never extracted for this version (unknown). That differs from status "absent" (the record has none)
    and from status "ok" with no arms or events (an empty module).
    """
    status = conn.execute(
        "SELECT safety_status FROM snapshots WHERE nct_id = %s AND content_hash = %s", (nct_id, content_hash),
    ).fetchone()
    if status is None or status[0] is None:
        return None
    if status[0] == "absent":
        return SafetyExtraction(status="absent")
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT frequency_threshold, time_frame, description FROM safety_module WHERE nct_id = %s AND content_hash = %s",
            (nct_id, content_hash))
        module = cur.fetchone() or {}
        cur.execute(
            "SELECT nct_id, content_hash, position, group_id, title, description, deaths_affected, deaths_at_risk, "
            "serious_affected, serious_at_risk, other_affected, other_at_risk, looks_pooled "
            "FROM safety_arms WHERE nct_id = %s AND content_hash = %s ORDER BY position", (nct_id, content_hash))
        arms = [SafetyArm(**row) for row in cur.fetchall()]
        cur.execute(
            "SELECT nct_id, content_hash, kind, position, term, notes, organ_system, vocabulary, assessment_type "
            "FROM safety_events WHERE nct_id = %s AND content_hash = %s ORDER BY kind DESC, position",   # serious before other
            (nct_id, content_hash))
        event_rows = cur.fetchall()
        cur.execute(
            "SELECT kind, event_position, group_id, affected, at_risk, events FROM safety_event_counts "
            "WHERE nct_id = %s AND content_hash = %s ORDER BY kind, event_position, position", (nct_id, content_hash))
        counts: dict[tuple, list] = {}
        for row in cur.fetchall():
            counts.setdefault((row["kind"], row["event_position"]), []).append(
                SafetyEventCount(group_id=row["group_id"], affected=row["affected"], at_risk=row["at_risk"], events=row["events"]))
    events = [SafetyEvent(**row, counts=counts.get((row["kind"], row["position"]), [])) for row in event_rows]
    return SafetyExtraction(status="ok", arms=arms, events=events, **module)


def save_papers(
    conn: psycopg.Connection,
    papers: list[Paper],
    links: list[PaperLink],
    raw_hash: str,
    raw_file_path: str,
    source_url: str,
    retrieved_at: datetime,
) -> int:
    """Save the papers read from ONE saved PubMed file, and their trial links. Returns how many papers were new.

    A paper is identified by (pmid, raw_hash): the same file saved twice adds nothing,
    a newer file gives new rows. Every link must name a paper from this call.
    """
    pmids = {paper.pmid for paper in papers}
    for link in links:
        if link.pmid not in pmids:
            raise ValueError(f"link for paper {link.pmid} has no paper in this save")

    new_papers = 0
    with conn.transaction():                                # papers and links: both saved or neither
        for paper in papers:
            row = {**paper.model_dump(), "raw_hash": raw_hash, "raw_file_path": raw_file_path,
                   "source_url": source_url, "retrieved_at": retrieved_at}
            cursor = conn.execute(
                """
                INSERT INTO papers (
                    pmid, raw_hash, title, abstract, journal, year, listed_nct_ids,
                    raw_file_path, source_url, retrieved_at
                ) VALUES (
                    %(pmid)s, %(raw_hash)s, %(title)s, %(abstract)s, %(journal)s, %(year)s, %(listed_nct_ids)s,
                    %(raw_file_path)s, %(source_url)s, %(retrieved_at)s
                )
                ON CONFLICT (pmid, raw_hash) DO NOTHING
                """,
                row,
            )
            new_papers += cursor.rowcount                   # 1 if inserted, 0 if it already existed

        for link in links:
            conn.execute(
                """
                INSERT INTO paper_links (nct_id, pmid, raw_hash, status, shared, other_nct_ids, reason)
                VALUES (%(nct_id)s, %(pmid)s, %(raw_hash)s, %(status)s, %(shared)s, %(other_nct_ids)s, %(reason)s)
                ON CONFLICT DO NOTHING
                """,
                {**link.model_dump(), "raw_hash": raw_hash},
            )
    return new_papers


def save_paper_search(
    conn: psycopg.Connection,
    search: PubMedSearch,
    attempts: int,
    batches_total: int = 0,
    batches_failed: int = 0,
    error_code: str | None = None,
) -> None:
    """Record that we searched PubMed for a trial, and how it went. Insert-only history."""
    conn.execute(
        """
        INSERT INTO paper_searches (
            nct_id, query, searched_at, status, total_count, pmids_found, truncated,
            attempts, batches_total, batches_failed, error_code
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (search.nct_id, search.query, search.retrieved_at, search.status, search.total_count, len(search.pmids),
         search.truncated, attempts, batches_total, batches_failed, error_code or search.error_code),
    )


def get_latest_paper_search(conn: psycopg.Connection, nct_id: str) -> dict | None:
    """The most recent PubMed search record for a trial. None = never searched (unknown)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM latest_paper_searches WHERE nct_id = %s", (nct_id,))
        return cur.fetchone()


def get_papers_for_trial(conn: psycopg.Connection, nct_id: str) -> list[dict]:
    """Every stored paper tied to one trial, with its link verdict. Newest download first, then by PMID.

    An empty list means nothing is stored: NOT that PubMed has no papers for the trial.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT p.pmid, p.raw_hash, p.title, p.abstract, p.journal, p.year, p.listed_nct_ids,
                   p.raw_file_path, p.source_url, p.retrieved_at,
                   l.status, l.shared, l.other_nct_ids, l.reason
            FROM paper_links l
            JOIN papers p ON p.pmid = l.pmid AND p.raw_hash = l.raw_hash
            WHERE l.nct_id = %s
            ORDER BY p.retrieved_at DESC, p.pmid
            """,
            (nct_id,),
        )
        return cur.fetchall()


def save_link_review(
    conn: psycopg.Connection,
    nct_id: str,
    pmid: str,
    raw_hash: str,
    relationship: str,
    reviewer: str,
    reason: str | None = None,
) -> None:
    """Add one person's answer about how a paper relates to a trial. Never changes an earlier answer."""
    conn.execute(
        "INSERT INTO paper_link_reviews (nct_id, pmid, raw_hash, relationship, reviewer, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (nct_id, pmid, raw_hash, relationship, reviewer, reason),
    )


def get_link_reviews(conn: psycopg.Connection, nct_id: str) -> dict[tuple[str, str], dict]:
    """The newest answer for each paper of one trial, keyed by (pmid, raw_hash). A paper not in it has no answer yet."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT pmid, raw_hash, relationship, reviewer, reason, reviewed_at "
            "FROM current_paper_link_reviews WHERE nct_id = %s",
            (nct_id,),
        )
        rows = cur.fetchall()
    return {(row["pmid"], row["raw_hash"]): row for row in rows}


def save_failure(conn: psycopg.Connection, nct_id: str, step: str, error_code: str, attempts: int) -> None:
    """Record a trial we could not load (rule 6: failures are never dropped)."""
    conn.execute(
        "INSERT INTO ingest_failures (nct_id, step, error_code, attempts) "
        "VALUES (%s, %s, %s, %s)",
        (nct_id, step, error_code, attempts),
    )


def results_by_sponsor(conn: psycopg.Connection) -> list[dict]:
    """Per sponsor key: how many COMPLETED trials in our database have / lack / don't know results.

    Counts the newest snapshot of each trial once. A sponsor with no name (NULL key)
    comes back as its own group, never merged with a real sponsor.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT sponsor_normalized,
                   MIN(sponsor)                                  AS example_name,
                   COUNT(*)                                      AS completed_trials,
                   COUNT(*) FILTER (WHERE has_results = true)    AS with_results,
                   COUNT(*) FILTER (WHERE has_results = false)   AS without_results,
                   COUNT(*) FILTER (WHERE has_results IS NULL)   AS unknown_results
            FROM latest_snapshots
            WHERE overall_status = 'COMPLETED'
            GROUP BY sponsor_normalized
            ORDER BY completed_trials DESC, sponsor_normalized
            """
        )
        return cur.fetchall()


def get_latest_snapshot(conn: psycopg.Connection, nct_id: str) -> Snapshot | None:
    """The newest stored version of one trial, or None if it was never stored."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM latest_snapshots WHERE nct_id = %(nct_id)s",
            {"nct_id": nct_id},
        )
        row = cur.fetchone()
    return Snapshot(**as_utc(row)) if row else None


MATCH_COLUMNS = (
    "match_run_id", "nct_id", "registry_hash", "outcome_position", "outcome_role", "outcome_text", "pmid", "paper_raw_hash",
    "system", "model", "prompt_hash", "schema_version", "status", "reported_role", "matched_name",
    "quote_text", "quote_start", "quote_end", "error_code", "reason", "raw_response",
    "input_tokens", "output_tokens", "seconds", "input_hash", "reused_from", "served_model",
)


def save_paper_matches(conn: psycopg.Connection, rows: list[dict]) -> None:
    """Save predictions about outcomes in paper abstracts. Insert-only: older rows are never changed.

    Each row is one system's answer (baseline or llm) for one outcome and one paper.
    """
    sql = f"INSERT INTO paper_matches ({', '.join(MATCH_COLUMNS)}) VALUES ({', '.join(['%s'] * len(MATCH_COLUMNS))})"
    with conn.transaction():                                # one trial's predictions: all saved or none
        for row in rows:
            values = [row.get(column) for column in MATCH_COLUMNS]
            conn.execute(sql, values)


def get_paper_matches(conn: psycopg.Connection, nct_id: str, match_run_id: str | None = None) -> list[dict]:
    """The stored predictions of one trial (the newest run by default), ordered for reading."""
    with conn.cursor(row_factory=dict_row) as cur:
        if match_run_id is None:
            cur.execute(
                "SELECT match_run_id FROM paper_matches WHERE nct_id = %s ORDER BY created_at DESC, match_id DESC LIMIT 1",
                (nct_id,),
            )
            newest = cur.fetchone()
            if newest is None:
                return []
            match_run_id = newest["match_run_id"]
        cur.execute(
            "SELECT * FROM paper_matches WHERE nct_id = %s AND match_run_id = %s ORDER BY outcome_role, outcome_position, pmid, system",
            (nct_id, match_run_id),
        )
        return cur.fetchall()


def save_skipped_papers(conn: psycopg.Connection, match_run_id: str, nct_id: str, papers: list[dict], reason: str = "paper_limit") -> None:
    """Record the listed papers a match run did not read, so the gap is visible. Insert-only."""
    with conn.transaction():
        for paper in papers:
            conn.execute(
                "INSERT INTO paper_match_skipped (match_run_id, nct_id, pmid, paper_raw_hash, title, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (match_run_id, nct_id, paper["pmid"], paper["raw_hash"], paper.get("title"), reason),
            )


def get_skipped_papers(conn: psycopg.Connection, nct_id: str, match_run_id: str) -> list[dict]:
    """The papers one match run did not read, lowest PMID first."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT pmid, title, reason FROM paper_match_skipped WHERE nct_id = %s AND match_run_id = %s ORDER BY pmid::bigint",
            (nct_id, match_run_id),
        )
        return cur.fetchall()


REUSABLE_STATUSES = ("identified", "not_identified", "ambiguous")        # a failed answer is retried, never reused


def find_reusable_match(conn: psycopg.Connection, input_hash: str, served_model: str | None = None) -> dict | None:
    """The newest stored LLM answer to exactly this question, if it is a usable answer (not a failure).

    With served_model given, only an answer made by that served model counts: if the provider has moved the model
    name to a newer version, old answers are not reused. served_model=None skips this check (the API could not be asked).
    """
    sql = "SELECT * FROM paper_matches WHERE system = 'llm' AND input_hash = %s AND status = ANY(%s)"
    params: list = [input_hash, list(REUSABLE_STATUSES)]
    if served_model is not None:
        sql += " AND served_model = %s"
        params.append(served_model)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql + " ORDER BY match_id DESC LIMIT 1", params)
        return cur.fetchone()


READING_COLUMNS = (
    "safety_run_id", "nct_id", "pmid", "paper_raw_hash", "system", "model", "served_model", "prompt_hash",
    "schema_version", "status", "document", "error_code", "reason", "raw_response", "input_tokens", "output_tokens",
    "seconds", "input_hash", "reused_from",
)
STATEMENT_COLUMNS = (
    "reading_id", "position", "kind", "term", "arm", "arm_in_quote", "affected", "denominator", "percent", "numbers",
    "grade", "treatment_related", "cause", "all_cause", "negated", "sentence", "quote_text", "quote_start", "quote_end",
    "rejected_code", "measure", "attribution_quote", "population", "population_scope_unverified",
    "event_scope", "evidence_group", "detail_level", "validation_notes",
)


def save_safety_reading(conn: psycopg.Connection, reading: dict, statements: list[dict]) -> int:
    """Save what one system read about safety in one abstract, with its statements. Insert-only. Returns the reading ID."""
    with conn.transaction():                                # the reading and its statements: all saved or none
        sql = (f"INSERT INTO safety_readings ({', '.join(READING_COLUMNS)}) "
               f"VALUES ({', '.join(['%s'] * len(READING_COLUMNS))}) RETURNING reading_id")
        reading_id = conn.execute(sql, [reading.get(column) for column in READING_COLUMNS]).fetchone()[0]
        sql = f"INSERT INTO safety_statements ({', '.join(STATEMENT_COLUMNS)}) VALUES ({', '.join(['%s'] * len(STATEMENT_COLUMNS))})"
        for position, statement in enumerate(statements):
            row = {"treatment_related": False, "all_cause": False, "negated": False,
                   "measure": "participant_proportion", "population_scope_unverified": False,
                   "event_scope": "unspecified", "detail_level": "unspecified", "validation_notes": [], **statement,
                   "reading_id": reading_id, "position": position}
            conn.execute(sql, [row.get(column) for column in STATEMENT_COLUMNS])
    return reading_id


def get_safety_readings(conn: psycopg.Connection, nct_id: str, safety_run_id: str | None = None) -> list[dict]:
    """The stored safety readings of one trial's papers (the newest run by default), each with its "statements" list."""
    with conn.cursor(row_factory=dict_row) as cur:
        if safety_run_id is None:
            cur.execute(
                "SELECT safety_run_id FROM safety_readings WHERE nct_id = %s ORDER BY created_at DESC, reading_id DESC LIMIT 1",
                (nct_id,),
            )
            newest = cur.fetchone()
            if newest is None:
                return []
            safety_run_id = newest["safety_run_id"]
        cur.execute(
            "SELECT * FROM safety_readings WHERE nct_id = %s AND safety_run_id = %s ORDER BY pmid::bigint, system",
            (nct_id, safety_run_id),
        )
        readings = cur.fetchall()
        for reading in readings:
            cur.execute("SELECT * FROM safety_statements WHERE reading_id = %s ORDER BY position", (reading["reading_id"],))
            reading["statements"] = cur.fetchall()
    return readings


def find_reusable_safety_reading(conn: psycopg.Connection, input_hash: str, served_model: str | None = None) -> dict | None:
    """The newest stored model answer to exactly this question, if the model gave a usable answer (status ok).

    Same rule as find_reusable_match: with served_model given, only an answer made by that served model counts.
    """
    sql = "SELECT * FROM safety_readings WHERE system = 'llm' AND input_hash = %s AND status = 'ok' AND raw_response IS NOT NULL"
    params: list = [input_hash]
    if served_model is not None:
        sql += " AND served_model = %s"
        params.append(served_model)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql + " ORDER BY reading_id DESC LIMIT 1", params)
        return cur.fetchone()


def get_evidence_by_ids(conn: psycopg.Connection, evidence_ids: list[str]) -> dict[str, Evidence]:
    """The evidence rows with these IDs, as {id: Evidence}. An ID that is not stored is simply absent."""
    if not evidence_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM evidence WHERE evidence_id = ANY(%s)", (list(evidence_ids),))
        rows = cur.fetchall()
    return {row["evidence_id"]: Evidence(**row) for row in rows}
