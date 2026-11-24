"""TrialTrace dashboard, step 1: enter an NCT ID, run the pipeline, see outcomes, papers and findings.

Run:  streamlit run dashboard/app.py
Every result here is AI-identified until a person reviews it.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# `streamlit run dashboard/app.py` puts only the dashboard folder on the import path, so add the project folder
# (no `pip install` needed to run the dashboard)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import psycopg
import streamlit as st

from trialtrace.checks.patterns import run_all
from trialtrace.checks.track_record import track_record
from trialtrace.cli import USER_AGENT, ingest_many
from trialtrace.compare.registry_changes import check_registry_changes, describe, describe_review
from trialtrace.config import Settings
from trialtrace.ingest_papers import PaperIngestResult, ingest_papers
from trialtrace.ingest_registry import ingest_trial
from trialtrace.match_papers import estimate_match_calls, match_trial
from trialtrace.paper_reader import probe_served_model, reader_settings
from trialtrace.trial_view import load_abstract, load_outcomes, load_papers, query
from trialtrace.pipeline_status import pipeline_status
from trialtrace.review.export_findings import export_rows, to_csv, to_markdown
from trialtrace.review.highlight import highlighted_markdown, numbered_markdown
from trialtrace.review.match_review import (STATES, UNRESOLVED_REASONS, count_states, counts_by_trial, filter_rows, load_by_match_ids,
                                            load_queue, outcome_key, outcome_overview, save_assisted_review, save_review, sort_by_risk,
                                            sort_overview, OUTCOME_STATUS_NAMES)


AUTO_RUN_LIMIT = 5      # up to this many new model calls run at once; more needs a confirm click


def fetch_trial(conn, settings, nct_id):
    """Download the trial and find its papers. Free: no model call."""
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as http:
        ingest_many(conn, http, settings, [nct_id])
        return ingest_many(conn, http, settings, [nct_id], ingest_papers, PaperIngestResult)[0]


def run_matching(conn, settings, nct_id, served_model):
    """Ask the model about every outcome and paper. Costs model calls; answers already stored are reused."""
    run_id = f"match-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    summary = match_trial(conn, settings, nct_id, run_id, served_model=served_model, include_baseline=False)
    conn.commit()
    return summary


def say_result(summary):
    st.info(f"Analysis finished: {summary.status}, {summary.pairs} outcome-paper pairs, "
            f"{summary.llm_calls} new model calls, {summary.llm_reused} reused.")


def analyze(conn, settings, nct_id):
    """Fetch the trial (free), work out the paid calls, run them if few, otherwise wait for a confirm click."""
    papers = fetch_trial(conn, settings, nct_id)
    if papers.status in ("search_failed", "fetch_failed", "error"):
        st.error(f"Paper ingestion failed: {papers.status} ({papers.detail or 'unknown error'}). Please retry. This does not mean no papers exist.")
        return
    if papers.status == "no_papers_found":
        st.info("PubMed returned no papers for this trial ID. This does not mean no publications exist.")
        return
    if papers.status == "partial":
        st.warning(f"Some paper batches could not be loaded ({papers.detail}). Results may be incomplete.")
    served_model = probe_served_model(reader_settings(settings))        # about 20 tokens: which model version is served now
    estimate = estimate_match_calls(conn, settings, nct_id, served_model=served_model)
    if estimate.status != "ok":
        st.info(f"Nothing to ask the model: {estimate.status}.")
    elif estimate.new_calls <= AUTO_RUN_LIMIT:
        say_result(run_matching(conn, settings, nct_id, served_model))
    else:
        st.session_state["pending"] = {"nct_id": nct_id, "served_model": served_model, "pairs": estimate.pairs,
                                       "reused": estimate.reused, "new_calls": estimate.new_calls}


def show_pending(conn, settings, nct_id):
    """A big run waits here until the user confirms it."""
    pending = st.session_state.get("pending")
    if not pending or pending["nct_id"] != nct_id:
        return
    st.warning(f"This will make {pending['new_calls']} new model calls ({pending['pairs']} pairs, {pending['reused']} already stored and reused). "
               f"Runs of {AUTO_RUN_LIMIT} calls or fewer start without asking.")
    run_it, cancel = st.columns(2)
    if run_it.button(f"Run {pending['new_calls']} model calls", type="primary"):
        del st.session_state["pending"]                                 # cleared first, so a redraw can never run it twice
        with st.spinner("Asking the model..."):
            say_result(run_matching(conn, settings, nct_id, pending["served_model"]))
    if cancel.button("Cancel"):
        del st.session_state["pending"]
        st.rerun()


SHOW_LIMIT = 25          # findings drawn per list; each one is a set of widgets, so a long list would be slow
STATE_NAMES = {"needs_review": "Needs review", "human_verified": "Human-verified", "unresolved": "Unresolved"}
STATE_DOTS = {"needs_review": "🟠", "human_verified": "🟢", "unresolved": "⚪"}
# colored labels; the colors only mark the model's answer, they say nothing about clinical risk
STATUS_BADGE = {"identified": ":green-badge[identified]", "ambiguous": ":orange-badge[ambiguous]", "not_identified": ":red-badge[not identified]"}
ROLE_BADGE = {"primary": ":blue-badge[primary]", "secondary": ":violet-badge[secondary]", "other": ":gray-badge[other]"}


def show_queue(conn, queue, reviewer, scope, only_key=None):
    """Three counts (each one a button that opens its findings), then the findings with review controls.

    scope ("one" or "all") keeps the widget keys of the two tabs apart.
    """
    counts = count_states(queue)
    chosen = st.session_state.get(f"queue_state_{scope}", "needs_review")
    columns = st.columns(3)
    for column, state in zip(columns, STATES):
        if column.button(f"{STATE_DOTS[state]} {STATE_NAMES[state]}: {counts[state]}", key=f"{scope}_open_{state}", width="stretch",
                         type="primary" if state == chosen else "secondary"):
            st.session_state[f"queue_state_{scope}"] = state
            chosen = state
    show_downloads(queue, scope)
    if not reviewer:
        st.warning("Enter your reviewer name in the sidebar to review.")
    rows = [row for row in queue if row["state"] == chosen]
    if only_key is not None:                                  # one outcome picked in the overview; the counts above stay for the whole trial
        rows = [row for row in rows if outcome_key(row) == only_key]
    rows = apply_filters(rows, scope)
    if st.toggle("Sort by risk (unclear first)", value=True, key=f"{scope}_risk"):
        rows = sort_by_risk(rows)
    if not rows:
        st.write(f"Nothing is {STATE_NAMES[chosen].lower()} with these filters.")
    for row in rows[:SHOW_LIMIT]:
        show_finding(conn, row, reviewer, scope)
    if len(rows) > SHOW_LIMIT:
        st.caption(f"Showing the first {SHOW_LIMIT} of {len(rows)}. Open a trial in the One trial view for the rest.")


STATE_ICONS = {"ok": "✅ ok", "warning": "⚠️ warning", "failed": "❌ failed", "not_started": "⚪ not started",
               "no_eligible": "➖ nothing to read", "awaiting_review": "⏳ awaiting review", "not_recorded": "➖ not recorded",
               "running": "🔄 running", "overdue": "⏱ completion overdue (possibly interrupted)"}


def show_pipeline_status(conn, nct_id):
    """One line per stage for this trial: where it is, where it stopped, with dates and run IDs. Read-only."""
    st.subheader("Pipeline status")
    lines = pipeline_status(conn, nct_id)
    table = [{"Stage": item["stage"], "State": STATE_ICONS.get(item["state"], item["state"]), "What happened": item["summary"],
              "When": item["when"], "Run": item["run_id"], "Last logged attempt": item["last_logged"]} for item in lines]
    st.dataframe(table, width="stretch", hide_index=True)
    st.caption("Each line shows the newest facts with their dates; an old failure is not shown as the current state. "
               "'Nothing to read' and 'not started' are different from 'failed'. Unclear model answers are findings, not failures.")


def show_outcome_overview(queue):
    """One row per registered outcome, built from ALL the trial's findings. Returns the key of the picked outcome, or None."""
    st.subheader("Outcome overview")
    order = st.radio("Order", ["Registry order", "Review priority"], horizontal=True, key="overview_order", label_visibility="collapsed")
    lines = sort_overview(outcome_overview(queue), by_priority=(order == "Review priority"))
    table = []
    for line in lines:
        counts = line["counts"]
        parts = [f"{counts[name]} {name.replace('_', ' ')}" for name in ("identified", "ambiguous", "not_identified") if counts[name]]
        table.append({"Role": f"{line['role'].capitalize()} {line['position'] + 1}", "Registered outcome": line["text"],
                      "Outcome status": OUTCOME_STATUS_NAMES.get(line["status"], line["status"]),
                      "Paper evidence": f"{line['papers']} paper(s): " + ", ".join(parts),
                      "Review progress": f"{line['reviewed']}/{line['papers']} reviewed"})
    event = st.dataframe(table, width="stretch", hide_index=True, key="overview_table", selection_mode="single-row", on_select="rerun",
                         column_config={"Registered outcome": st.column_config.TextColumn(width="large")})
    st.caption("Outcome status uses the same roll-up as the report: identified if any paper has an identified match, unclear if only ambiguous. "
               "Click a row to show only that outcome's findings below; click it again to clear. Nothing is approved at outcome level.")
    selected = event.selection.rows
    return lines[selected[0]]["key"] if selected else None


def apply_filters(rows, scope):
    """Three small filters above a list: the model's status, the outcome role and (all trials) part of an NCT ID."""
    status_column, role_column, trial_column = st.columns(3)
    statuses = status_column.multiselect("Model status", ["identified", "ambiguous", "not_identified"], key=f"{scope}_status",
                                         help="What the model concluded about whether the outcome is reported. It is NOT clinical risk or importance.")
    roles = role_column.multiselect("Outcome role", ["primary", "secondary", "other"], key=f"{scope}_role")
    nct_text = trial_column.text_input("Trial contains", key=f"{scope}_nct") if scope == "all" else ""
    kept = filter_rows(rows, statuses, roles, nct_text)
    if len(kept) != len(rows):
        st.caption(f"{len(kept)} of {len(rows)} findings match the filters.")
    return kept


def show_downloads(queue, scope):
    """Download buttons for every finding in this list (all three states), with the review decisions."""
    title = "all trials" if scope == "all" else queue[0]["nct_id"]
    rows = export_rows(queue)
    csv_column, markdown_column, _ = st.columns([1, 1, 4])
    csv_column.download_button("Download CSV", ("\ufeff" + to_csv(rows)).encode("utf-8"),          # BOM: Excel then reads accents and dashes correctly
                                file_name=f"trialtrace-findings-{title.replace(' ', '-')}.csv",
                               mime="text/csv", key=f"{scope}_csv")
    markdown_column.download_button("Download report", to_markdown(rows, title), file_name=f"trialtrace-findings-{title.replace(' ', '-')}.md",
                                    mime="text/markdown", key=f"{scope}_md")


def show_finding(conn, row, reviewer, scope):
    status_badge = STATUS_BADGE.get(row["status"], row["status"])
    role_badge = ROLE_BADGE.get(row["outcome_role"], row["outcome_role"])
    label = f"{status_badge} {role_badge} {row['nct_id']}  |  {row['outcome_position'] + 1}: {row['outcome_text'][:80]}  |  PMID {row['pmid']}"
    with st.expander(label):
        st.write(f"**Trial** {row['nct_id']}  |  **Paper** PMID {row['pmid']}")
        registered = load_registered(conn, row)
        st.write(f"**Registered outcome ({row['outcome_role']}):** {registered['measure_text']}")
        st.write(f"**Registered time frame:** {registered['time_frame'] or 'not given'}")
        if registered["description"]:
            st.write(f"**Registered description:** {registered['description']}")
        st.write(f"**Model finding:** {status_badge}  |  matched name: {row['matched_name'] or '-'}")
        if row["quote_text"]:
            st.write(f"**Source quote:** {row['quote_text']}")
        explanation = row["reason"] or model_mismatch(conn, row["match_id"])
        if explanation:
            st.write(f"**Model explanation:** {explanation}")
        elif row["status"] == "identified" and row["quote_text"]:
            st.write(f"**Evidence summary:** The model marked this outcome as identified and selected "
                     f"'{row['matched_name'] or 'an outcome'}' in the source quote above.")
            st.caption("This summary uses the saved result fields. This model response format saves a matching quote, "
                       "rather than a separate explanation for identified findings. Check the abstract against the registered details before accepting.")
        elif row["status"] == "not_identified":
            st.write("**Result summary:** The model marked this outcome as not identified in the saved abstract.")
            st.caption("No separate explanation was saved. This result does not establish whether the full paper reports the outcome.")
        else:
            st.warning("No model explanation was saved. Review the abstract and registered details before deciding.")
        if row["status"] == "ambiguous":
            st.info("The model could not establish an exact match. Compare the measure, assessor, population and time frame with the saved abstract. "
                    "If it remains unclear, choose Unable to determine with a reason and note. "
                    "Accept model finding confirms the ambiguous status; it does not establish that the outcome was reported.")
        show_abstract(conn, row)
        st.caption(f"Model {row['model']} (served as {row['served_model'] or 'unknown'}), prompt {str(row['prompt_hash'])[:10]}, "
                   f"schema {row['schema_version']}, registry {row['registry_hash'][:10]}, paper file {row['paper_raw_hash'][:10]}, run {row['match_run_id']}")
        if row["review_id"] is not None and row["state"] != "needs_review" or row["decision"] == "correct" and row["state"] == "needs_review":
            show_last_review(row)
        show_ai_review(row)

        key = f"{scope}_{row['match_id']}"
        note = st.text_input("Reviewer note", key=f"note_{key}")
        reason = st.selectbox("If unable to determine, why? (nothing is chosen for you)", list(UNRESOLVED_REASONS), format_func=UNRESOLVED_REASONS.get,
                              index=None, placeholder="Choose a reason", key=f"reason_{key}")
        pending = row["decision"] == "correct" and row["state"] == "needs_review"
        accept_col, correct_col, unable_col = st.columns(3)
        if pending:
            if accept_col.button("Accept the correction", key=f"acc_corr_{key}", disabled=not reviewer):
                record(conn, row, reviewer, "accept_correction")
        elif accept_col.button("Accept model finding", key=f"acc_{key}", disabled=not reviewer):
            record(conn, row, reviewer, "accept", note)
        if unable_col.button("Unable to determine", key=f"unable_{key}", disabled=not reviewer):
            record(conn, row, reviewer, "undetermined", note, unresolved_reason=reason)
        with correct_col.popover("Correct"):
            status = st.selectbox("Correct status", ("identified", "not_identified", "ambiguous"), key=f"cs_{key}")
            name = st.text_input("Correct matched name (optional)", key=f"cn_{key}")
            if st.button("Save correction (not verified until accepted)", key=f"corr_{key}", disabled=not reviewer):
                record(conn, row, reviewer, "correct", note, status, name.strip() or None)


def show_abstract(conn, row):
    """Show source evidence for every status, including findings without a quote."""
    abstract = load_abstract(conn, row["pmid"], row["paper_raw_hash"])
    if not abstract:
        st.warning("The saved abstract is missing. Check the source before reviewing this finding.")
        return
    marked = None
    if row["status"] == "identified" and row["quote_text"]:
        marked = highlighted_markdown(abstract, row["quote_text"], row["quote_start"], row["quote_end"])
        if marked is None:
            st.warning("The quote could not be found in the saved abstract, so nothing is highlighted. Do not accept this finding without checking.")
    with st.container(border=True):
        st.caption("Saved abstract, quote highlighted" if marked is not None else "Saved abstract, sentences numbered")
        st.markdown(marked if marked is not None else numbered_markdown(abstract))
    st.link_button("Open paper on PubMed", f"https://pubmed.ncbi.nlm.nih.gov/{row['pmid']}/")


def show_ai_review(row):
    """An AI reviewer's decision, shown for reference. It never makes a finding Human-verified."""
    if not row.get("ai_reviewer"):
        return
    text = f"AI review by {row['ai_reviewer']} (does not count as human verification): **{row['ai_decision']}**"
    if row["ai_corrected_status"]:
        text += f", settled as **{row['ai_corrected_status']}**"
    if row["ai_reason"]:
        text += f", unclear because: {UNRESOLVED_REASONS.get(row['ai_reason'], row['ai_reason'])}"
    if row["ai_evidence"]:
        text += f". Evidence: {row['ai_evidence']}"
    st.caption(text)


def show_last_review(row):
    text = f"Last review: **{row['decision']}** by {row['reviewer']} at {row['reviewed_at']:%Y-%m-%d %H:%M}"
    if row["corrected_status"]:
        text += f"  |  corrected to **{row['corrected_status']}** ({row['corrected_matched_name'] or 'no name'})"
    if row["note"]:
        text += f"  |  note: {row['note']}"
    st.info(text)


def record(conn, row, reviewer, decision, note="", status=None, name=None, unresolved_reason=None):
    """Save one review and redraw the page."""
    try:
        save_review(conn, row["match_id"], reviewer, decision, note, status, name, unresolved_reason=unresolved_reason)
        conn.commit()
    except ValueError as error:
        conn.rollback()
        st.error(str(error))
        return
    st.rerun()


def set_trial(table_key, lines):
    """Callback: a row clicked in the trial table becomes the trial of the One trial view."""
    picked = st.session_state[table_key]["selection"]["rows"]
    if picked:
        st.session_state["nct_id"] = lines[picked[0]]["nct_id"]
        st.session_state["view"] = "One trial"          # open the trial straight away


def show_summary(conn, reviewer):
    """Counts over every stored trial (latest run of each), a table per trial, and the findings of the chosen count."""
    everything = load_queue(conn)
    if not everything:
        st.write("No model findings stored yet.")
        return
    lines = counts_by_trial(everything)
    st.caption(f"{len(lines)} trials with findings. Counts use the latest run of each trial.")
    show_queue(conn, everything, reviewer, "all")
    st.subheader("Trials")
    st.dataframe(lines, width="stretch", hide_index=True, key="trial_table", selection_mode="single-row",
                 on_select=lambda: set_trial("trial_table", lines))
    st.caption("Click a row to open that trial.")


ASSISTED_FOLDER = Path(__file__).resolve().parent.parent / "evals" / "validation_v1" / "assisted_review"
ASSISTED_CHOICES = {
    "identified": "The outcome IS reported in this abstract (identified)",
    "not_identified": "The outcome is NOT reported in this abstract (not identified)",
    "still_unclear": "Still unclear: the abstract cannot settle it",
    "unable": "I am unable to decide",
}


def load_registered(conn, row):
    """The registered outcome as saved with the registry version the model saw."""
    rows = query(conn, "SELECT measure_text, description, time_frame FROM registered_outcomes "
                       "WHERE nct_id = %s AND content_hash = %s AND role = %s AND position = %s",
                 (row["nct_id"], row["registry_hash"], row["outcome_role"], row["outcome_position"]))
    return rows[0] if rows else {"measure_text": row["outcome_text"], "description": None, "time_frame": None}


def model_mismatch(conn, match_id):
    """The mismatch the model named when it answered unclear (it is inside the saved raw answer)."""
    rows = query(conn, "SELECT raw_response FROM paper_matches WHERE match_id = %s", (match_id,))
    try:
        raw = rows[0]["raw_response"]
        result = json.loads(raw) if isinstance(raw, str) else raw
        mismatch = result.get("mismatch")
        return mismatch if isinstance(mismatch, str) and mismatch.strip() else None
    except (IndexError, TypeError, ValueError, AttributeError):
        return None


def show_assisted(conn, reviewer):
    """Assisted review of the model's unclear findings: a frozen list, one finding at a time, timed."""
    selection_file = ASSISTED_FOLDER / "selection.json"
    if not selection_file.exists():
        st.info("No frozen selection for assisted review was found.")
        return
    selection = json.loads(selection_file.read_text(encoding="utf-8"))
    rows = load_by_match_ids(conn, [item["match_id"] for item in selection["findings"]])
    todo = [row for row in rows if row["state"] == "needs_review"]
    st.warning("ASSISTED REVIEW. You can see the model's answer here. This measures whether the dashboard helps a reviewer reach a "
               "justified decision. It is not an independent accuracy test.")
    st.caption(f"{len(rows) - len(todo)} of {len(rows)} findings reviewed. The list and its order are frozen "
               f"(selection written {selection['frozen_at']}). Time is counted while a finding is on screen; work through them in one sitting.")
    st.progress((len(rows) - len(todo)) / max(len(rows), 1))
    if not todo:
        st.success("Every finding in the frozen list has been reviewed.")
        return
    if not reviewer:
        st.warning("Enter your reviewer name in the sidebar to review.")

    row = todo[0]
    if st.session_state.get("assist_id") != row["match_id"]:             # a new finding is on screen: start its clock
        st.session_state["assist_id"] = row["match_id"]
        st.session_state["assist_at"] = time.monotonic()
    registered = load_registered(conn, row)
    st.subheader(f"{row['nct_id']}  |  {row['outcome_role']} {row['outcome_position'] + 1}  |  PMID {row['pmid']}")
    st.markdown(f"**Registered outcome:** {registered['measure_text']}")
    st.markdown(f"**Time frame:** {registered['time_frame'] or 'not given'}")
    if registered["description"]:
        st.markdown(f"**Description:** {registered['description']}")
    st.info(f"Model answer: UNCLEAR. Mismatch the model named: {model_mismatch(conn, row['match_id']) or 'none given'}")
    abstract = load_abstract(conn, row["pmid"], row["paper_raw_hash"])
    with st.container(border=True):
        st.caption("Abstract, sentences numbered")
        st.markdown(numbered_markdown(abstract) if abstract else "The saved abstract is missing.")

    key = row["match_id"]
    choice = st.radio("Your decision", list(ASSISTED_CHOICES), format_func=ASSISTED_CHOICES.get, index=None, key=f"assist_choice_{key}")
    evidence = st.text_input("Evidence: the sentence number(s) or the words you relied on", key=f"assist_evidence_{key}")
    reason = st.selectbox("Why does it stay unclear? (for the last two choices; nothing is chosen for you)", list(UNRESOLVED_REASONS),
                          format_func=UNRESOLVED_REASONS.get, index=None, placeholder="Choose a reason", key=f"assist_reason_{key}")
    note = st.text_input("Note (optional, required if unable to decide)", key=f"assist_note_{key}")
    if st.button("Save and show the next finding", type="primary", disabled=not reviewer or choice is None, key=f"assist_save_{key}"):
        seconds = int(time.monotonic() - st.session_state["assist_at"])
        try:
            save_assisted_review(conn, row["match_id"], reviewer, choice, evidence, reason if choice in ("still_unclear", "unable") else None, note, seconds)
            conn.commit()
        except ValueError as error:
            conn.rollback()
            st.error(str(error))
            return
        st.rerun()


st.set_page_config(page_title="TrialTrace", layout="wide")
st.title(":green[TrialTrace]")
st.caption("Clinical Trial Evidence Intelligence Platform")
st.caption("Are the outcomes a trial registered the ones its papers report? All results are AI-identified and need review. "
           "Model status says what the model concluded about an outcome; it is not clinical risk or importance.")

settings = Settings()
if settings.database_url is None:
    st.error("TRIALTRACE_DATABASE_URL is not set (see .env)")
    st.stop()

def show_sidebar_info(settings):
    """Read-only sidebar: review progress, what the colors mean, and which model produced the findings."""
    with psycopg.connect(settings.database_url) as conn:
        queue = load_queue(conn)
    counts = count_states(queue)
    total = len(queue)
    done = counts["human_verified"] + counts["unresolved"]
    st.sidebar.subheader("Review progress")
    if total:
        st.sidebar.progress(done / total, text=f"{done} of {total} findings reviewed ({done * 100 // total}%)")
        st.sidebar.caption(f"🟠 Needs review {counts['needs_review']}  |  🟢 Verified {counts['human_verified']}  |  ⚪ Unresolved {counts['unresolved']}")
    else:
        st.sidebar.caption("No findings stored yet.")
    st.sidebar.subheader("Colors")
    st.sidebar.markdown(":green-badge[identified] :orange-badge[ambiguous] :red-badge[not identified]")
    st.sidebar.caption("These show what the model answered. They are not clinical risk.")
    st.sidebar.subheader("Model")
    st.sidebar.caption(f"Reader model: {settings.paper_reader_model}")
    if queue:
        latest = max(row["match_run_id"] for row in queue)
        st.sidebar.caption(f"Latest run: {latest}")


def start_login():
    """Button callback: open the Google sign-in, or remember why it could not."""
    try:
        st.login()
    except Exception as error:                              # for example the [auth] keys are missing
        st.session_state["login_error"] = f"Sign-in is not set up: {error}"


def require_login(settings):
    """Google sign-in plus an email allowlist. Returns the reviewer name (the signed-in email).

    Fails closed: with no allowed emails set, nobody gets in. Login can be turned off only on purpose (TRIALTRACE_DISABLE_LOGIN).
    """
    if settings.disable_login:
        st.sidebar.warning("Login is off. Use this only on your own computer.")
        return st.sidebar.text_input("Reviewer name").strip()
    allowed = [email.strip().lower() for email in settings.allowed_emails.split(",") if email.strip()]
    if not allowed:
        st.error("No allowed emails are set. Put TRIALTRACE_ALLOWED_EMAILS in .env.")
        st.stop()
    if not st.user.get("is_logged_in", False):             # also False when .streamlit/secrets.toml has no [auth] keys
        st.subheader("Please sign in")
        st.button("Sign in with Google", type="primary", on_click=start_login)
        if st.session_state.get("login_error"):
            st.error(st.session_state["login_error"])
        st.stop()
    email = str(st.user.get("email") or "").lower()
    if email not in allowed or st.user.get("email_verified") is False:
        st.error(f"{email or 'This account'} is not allowed to use TrialTrace.")
        st.button("Log out", on_click=st.logout)
        st.stop()
    st.sidebar.caption(f"Signed in as {email}")
    st.sidebar.button("Log out", on_click=st.logout)
    return email


reviewer = require_login(settings)
show_sidebar_info(settings)

CHANGE_BADGE = {"added": ":blue-badge[added]", "removed": ":blue-badge[removed]", "role_changed": ":violet-badge[role changed]",
                "time_frame_changed": ":violet-badge[time frame changed]", "description_changed": ":violet-badge[description changed]",
                "ambiguous": ":gray-badge[could not match]"}
SCOPE_NOTE = "Counts cover the trials stored in this database only, not everything a sponsor has run. They are not a score or a verdict."


def show_registry_changes(conn, nct_id):
    """Did the registered outcomes change between the two newest saved versions? Neutral colors: a change is a reason to look, not a verdict."""
    report = check_registry_changes(conn, nct_id)
    st.subheader("Registry changes")
    if report.status == "unknown":
        st.info(f"Cannot compare versions: {report.reason}")
        return
    st.caption(f"Older version captured {report.older_captured:%Y-%m-%d} (registry update {report.older_registry_update or 'unknown'}), "
               f"newer captured {report.newer_captured:%Y-%m-%d} (registry update {report.newer_registry_update or 'unknown'}).")
    if report.status == "no_changes":
        st.write("The registered outcomes are the same in both versions.")
    for change in report.changes:
        st.markdown(f"{CHANGE_BADGE[change.kind]} {describe(change)}")
    for item in report.needs_review:
        st.markdown(f"{CHANGE_BADGE['ambiguous']} {describe_review(item)}")
    if report.changes or report.needs_review:
        st.caption("A change is a reason to look, not proof that anything was hidden. Outcomes that could not be matched safely are never counted as changes.")


def show_sponsors(conn):
    """Results reporting by sponsor: plain counts, no score and no ranking."""
    st.subheader("Results reporting by sponsor")
    st.caption("For each sponsor: completed trials that have posted results, none, or unknown. " + SCOPE_NOTE)
    records = track_record(conn)
    minimum = st.number_input("Show sponsors with at least this many completed trials", min_value=1, max_value=50, value=3, key="sponsor_min")
    name_part = st.text_input("Sponsor contains", key="sponsor_name").strip().lower()
    lines = []
    for record in records:
        name = record.example_name or "Unknown sponsor"
        if record.completed_trials < minimum or name_part not in name.lower():
            continue
        lines.append({"sponsor": name, "completed_trials": record.completed_trials, "with_results": record.with_results,
                      "without_results": record.without_results, "unknown": record.unknown_results})
    st.caption(f"{len(lines)} sponsors shown. Sorted by number of completed trials, not by anything about quality.")
    st.dataframe(lines[:300], width="stretch", hide_index=True)


def show_patterns(conn):
    """The fixed pattern questions over every stored trial. Lists of trials to look at, never verdicts."""
    st.subheader("Patterns across stored trials")
    st.caption("Each pattern is one fixed question. The results are things to look at, not findings about anyone. " + SCOPE_NOTE)
    for result in run_all(conn):
        st.markdown(f"**{result.description}**")
        has_trials = bool(result.rows) and "nct_id" in result.rows[0]
        if not result.rows:
            st.write("No trials match.")
        elif has_trials:
            st.caption(f"{len(result.rows)} trials. Click a row to open that trial.")
            key = f"pattern_{result.name}"
            st.dataframe(result.rows, width="stretch", hide_index=True, key=key, selection_mode="single-row",
                         on_select=lambda key=key, rows=result.rows: set_trial(key, rows))
        else:
            st.dataframe(result.rows, width="stretch", hide_index=True)


# A row clicked in a trial table must be able to switch the view, which st.tabs cannot do, so the views are buttons in a row
VIEWS = ["All trials", "One trial", "Assisted review", "Sponsors", "Patterns"]
view = st.segmented_control("View", VIEWS, default="All trials", key="view", label_visibility="collapsed") or "All trials"
if view == "All trials":
    with psycopg.connect(settings.database_url) as summary_conn:
        show_summary(summary_conn, reviewer)

if view == "Assisted review":
    with psycopg.connect(settings.database_url) as assisted_conn:
        show_assisted(assisted_conn, reviewer)

if view == "Sponsors":
    with psycopg.connect(settings.database_url) as sponsor_conn:
        show_sponsors(sponsor_conn)

if view == "Patterns":
    with psycopg.connect(settings.database_url) as pattern_conn:
        show_patterns(pattern_conn)

if view == "One trial":
    if "nct_id" not in st.session_state:                  # a view that is not drawn forgets its widgets, so bring the trial back
        st.session_state["nct_id"] = st.session_state.get("current_nct", "")
    nct_id = st.text_input("NCT ID", placeholder="NCT04644237", key="nct_id").strip().upper()
    st.session_state["current_nct"] = nct_id
    analyze_clicked = st.button("Analyze Trial", type="primary")

    if nct_id and not re.fullmatch(r"NCT[0-9]{8}", nct_id):
        st.warning("An NCT ID looks like NCT followed by 8 digits.")
    elif nct_id:
        with psycopg.connect(settings.database_url) as conn:
            if analyze_clicked:
                with st.spinner("Downloading the trial and papers, then counting the model calls..."):
                    analyze(conn, settings, nct_id)
            show_pending(conn, settings, nct_id)
            estimate = estimate_match_calls(conn, settings, nct_id)         # no served-model check here, so reuse may be overcounted
            if estimate.status == "ok":
                st.caption(f"A new Analyze would ask for at most {estimate.new_calls} new model calls "
                           f"({estimate.pairs} pairs, {estimate.reused} stored). Papers per trial are capped at {settings.paper_reader_max_papers}.")

            show_pipeline_status(conn, nct_id)
            outcomes = load_outcomes(conn, nct_id)
            if not outcomes:
                st.info("This trial is not stored yet. Click Analyze Trial.")
                st.stop()

            st.subheader("Registered outcomes")
            st.dataframe(outcomes, width="stretch", hide_index=True)

            show_registry_changes(conn, nct_id)

            st.subheader("Linked papers (candidates)")
            papers = load_papers(conn, nct_id)
            if papers:
                st.dataframe(papers, width="stretch", hide_index=True)
            else:
                st.write("No papers stored.")

            queue = load_queue(conn, nct_id)
            if not queue:
                st.subheader("Review queue")
                st.write("No model findings yet.")
                st.stop()
            picked = show_outcome_overview(queue)
            st.subheader("Review queue")
            show_queue(conn, queue, reviewer, "one", only_key=picked)
            st.caption("'Not identified' means not found in the abstract. Full text, tables and figures were not read, so it does not mean the paper omits the outcome.")
