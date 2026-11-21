##############################################################################
# trialtrace/pipeline_status.py
#
# PURPOSE: One line per pipeline stage for one trial, so "where did this NCT ID get stuck?" has a quick answer.
#          Read-only. The facts come from the tables that hold them (snapshots, ingest_failures, paper_searches,
#          paper_matches, reviews); pipeline_events adds the last logged attempt, its duration and the match run ID.
#          Every line carries a date, and an old failure is never shown as the current state of a newer success.
#
# STATES:  ok | warning | failed | not_started | no_eligible | awaiting_review | not_recorded | running | overdue
#          "not_started" (nothing happened yet), "no_eligible" (it ran, but there was nothing to read),
#          "awaiting_review" (waiting for a person) and "failed" (something broke) are kept apart on purpose.
#          "overdue" = a stage logged that it started and has not logged completion after OVERDUE_MINUTES. That is NOT
#          proof of a crash: the process may have stopped, or may still be working (there is no heartbeat to tell).
#          "running" = the same, but it started less than OVERDUE_MINUTES ago.
#
# USE:     lines = pipeline_status(conn, "NCT04644237")
##############################################################################

from psycopg.rows import dict_row

from trialtrace.review.match_review import NEEDS_REVIEW, load_queue

OVERDUE_MINUTES = 15

STAGE_NAMES = {"registry": "Registry", "raw_backup": "Raw backup", "papers": "Papers", "matching": "Matching",
               "findings": "Findings", "review": "Review"}


def day(moment) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "-"


def one(conn, sql: str, params: tuple) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def last_event(conn, nct_id: str, stage: str) -> dict | None:
    """The newest finished attempt of a stage (a completion event). None if there is none, or if the events table does not exist (yet)."""
    try:
        with conn.transaction():                                  # a savepoint: a missing table must not spoil the page's transaction
            return one(conn, "SELECT * FROM pipeline_events WHERE nct_id = %s AND stage = %s AND status <> 'started' "
                             "ORDER BY id DESC LIMIT 1", (nct_id, stage))
    except Exception:
        return None


def unfinished_start(conn, nct_id: str, stage: str) -> dict | None:
    """The newest "started" event that no completion event points to: the stage began and never reported back."""
    try:
        with conn.transaction():
            return one(conn, "SELECT s.*, now() - s.created_at AS age FROM pipeline_events s WHERE s.nct_id = %s AND s.stage = %s "
                             "AND s.status = 'started' AND NOT EXISTS (SELECT 1 FROM pipeline_events c WHERE c.started_id = s.id) "
                             # a later finished attempt of the same stage means the old crash is history, not the current state
                             "AND s.id > COALESCE((SELECT max(d.id) FROM pipeline_events d WHERE d.nct_id = s.nct_id AND d.stage = s.stage "
                             "AND d.status <> 'started'), 0) "
                             "ORDER BY s.id DESC LIMIT 1", (nct_id, stage))
    except Exception:
        return None


def with_interruption(conn, nct_id: str, stage: str, current: dict) -> dict:
    """If the stage has an unfinished start newer than the facts shown, say so first and keep the facts as 'earlier'."""
    start = unfinished_start(conn, nct_id, stage)
    if start is None:
        return current
    minutes = start["age"].total_seconds() / 60
    state = "running" if minutes < OVERDUE_MINUTES else "overdue"
    what = ("started and has not finished yet" if state == "running"
            else f"started and has not reported completion after {minutes:.0f} minutes. It may have crashed or been stopped, "
                 "or it may still be working: nothing here can tell which")
    run = f", run {start['analysis_id']}" if start["analysis_id"] else ""
    summary = f"An attempt on {day(start['created_at'])}{run} {what}. Earlier facts: {current['summary']}"
    return {**current, "state": state, "summary": summary, "when": day(start["created_at"]), "run_id": start["analysis_id"] or current["run_id"]}


def line(stage: str, state: str, summary: str, when=None, run_id: str | None = None, event: dict | None = None) -> dict:
    last = ""
    if event:
        seconds = f", {event['duration_ms'] / 1000:.1f}s" if event["duration_ms"] is not None else ""
        code = f", {event['error_code']}" if event["error_code"] else ""
        last = f"{event['status']}{code}{seconds} on {day(event['created_at'])}"
    return {"stage": STAGE_NAMES[stage], "state": state, "summary": summary, "when": day(when), "run_id": run_id or "", "last_logged": last}


def registry_line(conn, nct_id: str) -> dict:
    snapshot = one(conn, "SELECT count(*) AS versions, max(retrieved_at) AS newest FROM snapshots WHERE nct_id = %s", (nct_id,))
    failure = one(conn, "SELECT step, error_code, failed_at FROM ingest_failures WHERE nct_id = %s ORDER BY failed_at DESC LIMIT 1", (nct_id,))
    event = last_event(conn, nct_id, "registry")
    has_snapshot = snapshot["versions"] > 0
    if not has_snapshot and not failure:
        return line("registry", "not_started", "Not downloaded yet.", event=event)
    if failure and (not has_snapshot or failure["failed_at"] > snapshot["newest"]):
        saved = f" The latest saved capture is {day(snapshot['newest'])}." if has_snapshot else " Nothing is saved."
        return line("registry", "failed", f"{failure['step']} failed: {failure['error_code']}.{saved}", failure["failed_at"], event=event)
    note = ""
    if failure:
        note = f" An older {failure['step']} failure ({failure['error_code']}, {day(failure['failed_at'])}) came before this capture."
    return line("registry", "ok", f"{snapshot['versions']} version(s) saved.{note}", snapshot["newest"], event=event)


def backup_line(conn, nct_id: str) -> dict:
    event = last_event(conn, nct_id, "raw_backup")
    if event is None:
        return line("raw_backup", "not_recorded", "No backup attempt recorded for this trial (backup off, or ingested before it was logged).")
    if event["status"] == "ok":
        return line("raw_backup", "ok", f"Copied to Cloud Storage ({event['detail'].get('source', 'source')}).", event["created_at"], event=event)
    return line("raw_backup", "warning", "The upload to Cloud Storage failed. The local copy exists; run the rsync command to catch up.",
                event["created_at"], event=event)


def papers_line(conn, nct_id: str) -> dict:
    search = one(conn, "SELECT * FROM latest_paper_searches WHERE nct_id = %s", (nct_id,))
    event = last_event(conn, nct_id, "papers")
    if search is None:
        return line("papers", "not_started", "PubMed has not been searched yet.", event=event)
    linked = one(conn, "SELECT count(DISTINCT pmid) AS papers FROM paper_links WHERE nct_id = %s", (nct_id,))["papers"]
    when = search["searched_at"]
    if search["status"] in ("unavailable", "invalid"):
        return line("papers", "failed", f"The PubMed search failed: {search['error_code']}. This does not mean no papers exist.", when, event=event)
    if search["status"] == "not_found":
        return line("papers", "no_eligible", "The search ran and PubMed returned no papers for this ID (this does not mean none exist).", when, event=event)
    if search["batches_failed"]:
        return line("papers", "warning", f"{linked} paper(s) linked; {search['batches_failed']} of {search['batches_total']} batch(es) could not be read "
                    f"({search['error_code']}). Results may be incomplete.", when, event=event)
    return line("papers", "ok", f"{search['pmids_found']} paper(s) found, {linked} linked.", when, event=event)


def latest_run(conn, nct_id: str) -> tuple[str | None, list[dict]]:
    """The newest match run of the model for this trial and its rows."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT max(match_run_id) AS run FROM paper_matches WHERE nct_id = %s AND system = 'llm'", (nct_id,))
        run = cur.fetchone()["run"]
        if run is None:
            return None, []
        cur.execute("SELECT status, error_code, created_at FROM paper_matches WHERE nct_id = %s AND system = 'llm' AND match_run_id = %s",
                    (nct_id, run))
        return run, cur.fetchall()


def matching_line(conn, nct_id: str, run: str | None, rows: list[dict], papers: dict) -> dict:
    event = last_event(conn, nct_id, "matching")
    if not rows:
        if event and event["status"] == "no_eligible":
            return line("matching", "no_eligible", "The model step ran, but there were no eligible papers with an abstract to read.",
                        event["created_at"], event["analysis_id"], event)
        if event and event["status"] == "failed":
            return line("matching", "failed", f"The model step failed: {event['error_code']}.", event["created_at"], event["analysis_id"], event)
        if papers["state"] == "no_eligible":
            return line("matching", "no_eligible", "No papers to read, so no outcome-paper pairs.")
        return line("matching", "not_started", "The model has not read this trial yet.", event=event)
    failed = [row for row in rows if row["status"] in ("failed", "no_text")]
    codes = sorted({row["error_code"] or row["status"] for row in failed})
    skipped = one(conn, "SELECT count(*) AS n FROM paper_match_skipped WHERE nct_id = %s AND match_run_id = %s", (nct_id, run))["n"]
    detail = event["detail"] if event and event["analysis_id"] == run else {}
    parts = [f"{len(rows)} pair(s)"]
    if "calls" in detail:
        parts.append(f"{detail['calls']} new call(s), {detail['reused']} reused")
    parts.append(f"{len(failed)} failed" + (f" ({', '.join(codes)})" if codes else ""))
    parts.append(f"{skipped} paper(s) skipped by the cap")
    state = "ok" if not failed else ("failed" if len(failed) == len(rows) else "warning")
    return line("matching", state, ", ".join(parts) + ".", max(row["created_at"] for row in rows), run, event)


def findings_line(rows: list[dict], run: str | None) -> dict:
    if not rows:
        return line("findings", "not_started", "No model findings yet.")
    counts = {name: sum(1 for row in rows if row["status"] == name) for name in ("identified", "ambiguous", "not_identified")}
    unknown = sum(1 for row in rows if row["status"] in ("failed", "no_text"))
    text = (f"{len(rows)} outcome-paper findings: {counts['identified']} identified, {counts['ambiguous']} unclear (ambiguous), "
            f"{counts['not_identified']} not identified, {unknown} unknown (model answer unusable). These are not registered outcomes.")
    return line("findings", "ok", text, max(row["created_at"] for row in rows), run)


def review_line(conn, nct_id: str, has_findings: bool) -> dict:
    if not has_findings:
        return line("review", "not_started", "Nothing to review yet.")
    queue = load_queue(conn, nct_id)
    waiting = sum(1 for row in queue if row["state"] == NEEDS_REVIEW)
    verified = sum(1 for row in queue if row["state"] == "human_verified")
    unresolved = sum(1 for row in queue if row["state"] == "unresolved")
    text = f"Of {len(queue)} outcome-paper findings: {verified} human-verified, {unresolved} unresolved, {waiting} waiting for a person."
    reviewed_at = [row["reviewed_at"] for row in queue if row["reviewed_at"]]
    return line("review", "awaiting_review" if waiting else "ok", text, max(reviewed_at) if reviewed_at else None)


def pipeline_status(conn, nct_id: str) -> list[dict]:
    """The six stage lines of one trial, in pipeline order."""
    registry = with_interruption(conn, nct_id, "registry", registry_line(conn, nct_id))
    backup = backup_line(conn, nct_id)
    papers_now = papers_line(conn, nct_id)
    papers = with_interruption(conn, nct_id, "papers", papers_now)
    run, rows = latest_run(conn, nct_id)
    matching = with_interruption(conn, nct_id, "matching", matching_line(conn, nct_id, run, rows, papers_now))
    findings = findings_line(rows, run)
    review = review_line(conn, nct_id, bool(rows))
    return [registry, backup, papers, matching, findings, review]
