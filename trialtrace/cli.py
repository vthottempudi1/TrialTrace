##############################################################################
# trialtrace/cli.py
#
# ---------------------------------------------------------------------------
# PURPOSE
# ---------------------------------------------------------------------------
# The command line for TrialTrace: how a person runs the system. It holds
# almost no logic of its own. Each command reads its options, calls the
# modules we already built, commits the database work, and prints a short
# summary.
#
#     ingest          ingest_registry.py download trials and store them
#     ingest-papers   ingest_papers.py   find and store the PubMed papers of stored trials
#     match-papers    match_papers.py    LLM + baseline predictions: are the outcomes reported in the abstracts?
#     read-safety     read_safety.py     LLM + baseline: which safety statements are in the abstracts? (compared in the report)
#     rekey-sponsors  db/repository.py   recompute sponsor grouping keys
#     backfill-safety backfill_safety.py fill in adverse-event data from saved raw files (no network)
#     backfill-paper-evidence  backfill_safety.py  save paper evidence of stored runs under IDs that hold the PMID (no network)
#     report          report.py          build a report from the stored trials
#     review          review/review.py   record decisions about a report's findings
#
# Key rules this file follows:
#   - The CLI is the one place that commits. The modules it calls never do, so
#     each command decides when its work is safely saved: ingest after every
#     trial, review after every decision.
#   - It never needs the network except for `ingest` and `ingest-papers`. Report and review read
#     only the database.
#   - One unexpected error in one trial does not stop an ingest batch (see
#     ingest_many); a stopped review loses no decision already made.
#
# ---------------------------------------------------------------------------
# COMMANDS
# ---------------------------------------------------------------------------
# ingest NCT... [--file ids.txt]
#     Reads the IDs (duplicates dropped, # comments skipped), ingests each trial,
#     prints one line per trial, then a summary and the count of missing values
#     across everything stored.
#
# ingest-papers [NCT... | --file ids.txt]
#     Searches PubMed for each trial and stores the papers with their link verdicts.
#     With no IDs it uses every trial already stored. Prints one line per trial
#     (papers found / new / failed batches), then a summary. Only `ingest` and
#     `ingest-papers` use the network.
#
# match-papers NCT... [--file ids.txt] [--all]
#     For each trial, reads its listed papers' abstracts with the frozen LLM reader and the code
#     baseline, stores BOTH answers separately, and prints how many pairs need review (disagreements,
#     unclear answers, failures). Calls the model API (cost): give IDs or --all. An answer already stored
#     for exactly the same question (outcome, time frame, abstract, model, prompt) is reused, not paid for
#     again; --refresh asks the model again. Every result is provisional until a person reviews it.
#
# rekey-sponsors
#     Run after the sponsor naming rules change, so stored grouping keys match.
#
# report [--as-of 2026-11-02] [--out-dir outputs/reports]
#     Builds the report, saves it as a new run (so it can be reviewed), writes
#     <run_id>.md into the folder, and prints the count of findings by category.
#
# review --reviewer "Name" [--run RUN_ID] [--all]
#     Goes through the findings of a run (the latest by default) that need a
#     decision and asks: approve, edit, reject, skip or quit. By default only
#     differences and items that need review are asked about; --all adds the
#     plain facts (results shown or not, how long ago completion was).
#
# ---------------------------------------------------------------------------
# USE
# ---------------------------------------------------------------------------
#   python -m trialtrace ingest NCT04644237 NCT01234567
#   python -m trialtrace ingest --file ids.txt
#   python -m trialtrace ingest-papers
#   python -m trialtrace report
#   python -m trialtrace review --reviewer "Dr Rao"
##############################################################################

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import psycopg

from trialtrace.safety.backfill_safety import backfill_paper_evidence, backfill_safety, backfill_safety_evidence
from trialtrace.checks.patterns import run_pattern
from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_run_id, list_trial_ids, recompute_sponsor_keys, save_run
from trialtrace.ingest_papers import PaperIngestResult, ingest_papers
from trialtrace.ingest_registry import IngestResult, ingest_trial
from trialtrace.match_papers import match_trial
from trialtrace.paper_reader import probe_served_model, reader_settings
from trialtrace.safety.read_safety import read_safety_for_trial
from trialtrace.report import build_report, new_run_id, render_text
from trialtrace.review.link_review import review_links
from trialtrace.review.review import review_run

USER_AGENT = "trialtrace/0.1 (research tool)"       # tells the registry who is asking


def read_ids(nct_ids: list[str], file_path: str | None) -> list[str]:
    """IDs from the command line plus an optional file (one per line; blank lines and # comments skipped).
    Duplicates are dropped, keeping the first."""
    wanted = list(nct_ids)
    if file_path:
        for line in Path(file_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                wanted.append(line)

    ids = []
    for nct_id in wanted:
        nct_id = nct_id.strip().upper()
        if nct_id not in ids:
            ids.append(nct_id)
    return ids


def ingest_many(conn, http, settings, ids, ingest_one=ingest_trial, result_type=IngestResult) -> list:
    """Ingest each trial in turn and commit after each one (registry or papers: pass the matching
    ingest_one and result_type).

    An unexpected error (disk, database...) is rolled back for that trial, reported as
    status "error", and the next trial still runs. If the rollback itself fails, the
    database is gone and the error is raised: the batch stops.
    """
    results = []
    for nct_id in ids:
        try:
            result = ingest_one(conn, http, settings, nct_id)
            conn.commit()                                       # one trial at a time: a later crash loses nothing
        except Exception as error:
            conn.rollback()
            result = result_type(nct_id=nct_id, status="error", detail=type(error).__name__)
        results.append(result)
    return results


def run_ingest(nct_ids: list[str], file_path: str | None) -> None:
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    ids = read_ids(nct_ids, file_path)
    if not ids:
        raise SystemExit("Give at least one NCT ID, or --file ids.txt")

    counts = Counter()
    with psycopg.connect(settings.database_url) as conn:
        with httpx.Client(headers={"User-Agent": USER_AGENT}) as http:
            for result in ingest_many(conn, http, settings, ids):
                counts[result.status] += 1
                detail = f"  ({result.detail})" if result.detail else ""
                print(f"{result.nct_id}  {result.status}  tries={result.attempts}{detail}")

        print()
        print("Summary: " + ", ".join(f"{status} {number}" for status, number in sorted(counts.items())))

        # Missing values across everything stored: a jump here can mean the registry changed its format
        unknown = run_pattern(conn, "unknown_values_by_field").rows[0]
        print(f"Stored trials: {unknown['trials']}  (in the ingested corpus only)")
        print(f"  unknown sponsor {unknown['unknown_sponsor']}, unknown results flag {unknown['unknown_has_results']}, "
              f"unknown completion date {unknown['unknown_completion_date']}, "
              f"unknown completion type {unknown['unknown_completion_type']}")


def run_ingest_papers(nct_ids: list[str], file_path: str | None) -> None:
    """Find and store the PubMed papers for the given trials (default: every stored trial)."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")

    counts = Counter()
    with psycopg.connect(settings.database_url) as conn:
        ids = read_ids(nct_ids, file_path) or list_trial_ids(conn)
        if not ids:
            raise SystemExit("No trials stored yet. Run: python -m trialtrace ingest NCT...")
        with httpx.Client(headers={"User-Agent": USER_AGENT}) as http:
            results = ingest_many(conn, http, settings, ids, ingest_papers, PaperIngestResult)
            for result in results:
                counts[result.status] += 1
                detail = f"  ({result.detail})" if result.detail else ""
                print(f"{result.nct_id}  {result.status}  found={result.papers_found} new={result.papers_new} "
                      f"failed_batches={result.batches_failed}{detail}")

        print()
        print("Summary: " + ", ".join(f"{status} {number}" for status, number in sorted(counts.items())))
        print("Papers are CANDIDATES: each has a link verdict (listed / text_only / not_confirmed).")


def run_match_papers(nct_ids: list[str], file_path: str | None, everything: bool, refresh: bool = False, with_baseline: bool = False) -> None:
    """Match outcomes with the LLM; optionally add a separately stored code cross-check."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    if settings.openai_api_key is None:
        raise SystemExit("OPENAI_API_KEY is not set (see .env): the paper reader needs it")
    run_id = f"match-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    agreements = Counter()
    risks = Counter()
    with psycopg.connect(settings.database_url) as conn:
        ids = read_ids(nct_ids, file_path)
        if everything:
            ids = list_trial_ids(conn)
        if not ids:
            raise SystemExit("Give NCT IDs, --file ids.txt, or --all (this calls the model API, so it needs a choice)")
        print(f"Match run {run_id}: model {settings.paper_reader_model}, at most {settings.paper_reader_max_papers} papers per trial")
        served_model = probe_served_model(reader_settings(settings))        # about 20 tokens: which model version is being served now?
        if served_model is None:
            print("Warning: could not ask the API which model version is served; stored answers are reused without that check.")
        else:
            print(f"Model version served now: {served_model} (stored answers from another version are not reused)")
        for nct_id in ids:
            try:
                summary = match_trial(conn, settings, nct_id, run_id, refresh=refresh, served_model=served_model, include_baseline=with_baseline)
                conn.commit()                                    # one trial at a time
            except Exception as error:
                conn.rollback()
                print(f"{nct_id}  error  ({type(error).__name__})")
                continue
            agreements.update(summary.agreements)
            risks.update(summary.risks)
            skipped = f", {summary.papers_skipped} paper(s) NOT read (limit): PMID {', '.join(summary.skipped_pmids)}" if summary.papers_skipped else ""
            print(f"{nct_id}  {summary.status}  papers={summary.papers_used} pairs={summary.pairs} "
                  f"model-calls={summary.llm_calls} reused={summary.llm_reused} "
                  f"high-risk={summary.risks.get('high', 0)}{skipped}")

    print()
    print(("Agreement: " if with_baseline else "Model outcomes (baseline disabled): ") + ", ".join(f"{name} {number}" for name, number in sorted(agreements.items())))
    print("Review priority: " + ", ".join(f"{name} {risks.get(name, 0)}" for name in ("high", "medium", "low")))
    print("All results are provisional until reviewed. 'Not identified' in an abstract does not mean the full paper omits the outcome.")


def run_read_safety(nct_ids: list[str], file_path: str | None, everything: bool, refresh: bool = False, with_baseline: bool = False) -> None:
    """Read the safety statements in paper abstracts with the LLM and the baseline, and store both answers."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    if settings.openai_api_key is None:
        raise SystemExit("OPENAI_API_KEY is not set (see .env): the safety reader needs it")
    run_id = f"safety-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    totals = Counter()
    with psycopg.connect(settings.database_url) as conn:
        ids = read_ids(nct_ids, file_path)
        if everything:
            ids = list_trial_ids(conn)
        if not ids:
            raise SystemExit("Give NCT IDs, --file ids.txt, or --all (this calls the model API, so it needs a choice)")
        print(f"Safety run {run_id}: model {settings.paper_reader_model}, one call per abstract, at most {settings.paper_reader_max_papers} papers per trial")
        served_model = probe_served_model(reader_settings(settings))        # about 20 tokens: which model version is being served now?
        if served_model is None:
            print("Warning: could not ask the API which model version is served; stored answers are reused without that check.")
        else:
            print(f"Model version served now: {served_model} (stored answers from another version are not reused)")
        for nct_id in ids:
            try:
                summary = read_safety_for_trial(conn, settings, nct_id, run_id, refresh=refresh, served_model=served_model, include_baseline=with_baseline)
                conn.commit()                                    # one trial at a time
            except Exception as error:
                conn.rollback()
                print(f"{nct_id}  error  ({type(error).__name__})")
                continue
            skipped = f", {summary.papers_skipped} paper(s) NOT read (limit): PMID {', '.join(summary.skipped_pmids)}" if summary.papers_skipped else ""
            print(f"{nct_id}  {summary.status}  papers={summary.papers_read} model-calls={summary.llm_calls} reused={summary.llm_reused} "
                  f"failed={summary.llm_failed} statements: model {summary.model_statements} (rejected {summary.model_rejected}), "
                  f"{'baseline ' + str(summary.baseline_statements) if with_baseline else 'baseline disabled'}{skipped}")
            for name in ("papers_read", "llm_calls", "llm_reused", "llm_failed", "model_statements", "model_rejected", "baseline_statements"):
                totals[name] += getattr(summary, name)

    print()
    print(f"Papers read: {totals['papers_read']}; model calls {totals['llm_calls']}, reused {totals['llm_reused']}, failed {totals['llm_failed']}")
    print(f"Statements: model {totals['model_statements']} passed the code checks and {totals['model_rejected']} were rejected (kept with the reason); "
          f"{'baseline ' + str(totals['baseline_statements']) if with_baseline else 'baseline disabled'}")
    print("Nothing is compared with the registry yet. All results are provisional until reviewed.")


def run_report(as_of_text: str | None, out_dir: str) -> None:
    """Build a report from the stored trials, save the run, write the text file, print a short summary."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    try:
        as_of = date.fromisoformat(as_of_text) if as_of_text else date.today()
    except ValueError:
        raise SystemExit(f"--as-of must look like 2026-11-02, got {as_of_text!r}")

    with psycopg.connect(settings.database_url) as conn:
        report = build_report(conn, as_of, new_run_id())
        save_run(conn, report.run_id, report.as_of, report.all_findings())
        conn.commit()

    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{report.run_id}.md"
    path.write_text(render_text(report), encoding="utf-8")

    counts = Counter(finding.category for finding in report.all_findings())
    print(f"Report {report.run_id}: {len(report.trials)} trial(s), {sum(counts.values())} finding(s)")
    for category in sorted(counts):
        print(f"  {category}: {counts[category]}")
    print(f"Written to {path}")


def run_review(reviewer: str, run_id: str | None, include_plain_facts: bool = False) -> None:
    """Ask the reviewer about each finding that needs a decision, saving every decision at once."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")

    with psycopg.connect(settings.database_url) as conn:
        chosen = run_id or get_latest_run_id(conn)
        if chosen is None:
            raise SystemExit("No report has been generated yet. Run: python -m trialtrace report")
        try:
            review_run(conn, chosen, reviewer, commit=conn.commit, include_plain_facts=include_plain_facts)
        except (EOFError, KeyboardInterrupt):
            print("\nStopped. Decisions already made are saved.")


def run_review_links(reviewer: str, nct_ids: list[str]) -> None:
    """Ask how each stored paper relates to its trial (default: every stored trial), saving every answer at once."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")

    with psycopg.connect(settings.database_url) as conn:
        chosen = nct_ids or list_trial_ids(conn)
        try:
            for nct_id in chosen:
                review_links(conn, nct_id, reviewer, commit=conn.commit)
        except (EOFError, KeyboardInterrupt):
            print("\nStopped. Answers already given are saved.")


def run_backfill_safety() -> None:
    """Read the adverse-event module of every stored version that has not been read yet (from the saved files)."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    with psycopg.connect(settings.database_url) as conn:
        result = backfill_safety(conn)
        evidence_added = backfill_safety_evidence(conn)
        conn.commit()
    print(f"Safety data filled in: {result.filled}; no safety module in the record: {result.absent}; "
          f"raw file missing: {result.missing_file}; unreadable: {result.parse_error}")
    print(f"Safety evidence rows added: {evidence_added}")


def run_backfill_paper_evidence() -> None:
    """Save the abstract and quote evidence of stored match runs and safety readings under the IDs that hold the PMID."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    with psycopg.connect(settings.database_url) as conn:
        added = backfill_paper_evidence(conn)
        conn.commit()
    print(f"Paper evidence rows added: {added}")


def run_rekey_sponsors() -> None:
    """Recompute the sponsor grouping keys of everything stored (run after the naming rules change)."""
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    with psycopg.connect(settings.database_url) as conn:
        changed = recompute_sponsor_keys(conn)
        conn.commit()
    print(f"Sponsor keys updated: {changed}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="trialtrace")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("backfill-paper-evidence", help="save abstract and quote evidence of stored runs under the IDs that hold the PMID (no network, safe to run twice)")
    commands.add_parser("backfill-safety", help="read adverse-event data from the saved raw files of versions stored before safety existed (no network)")
    commands.add_parser("rekey-sponsors", help="recompute sponsor grouping keys after the naming rules change")

    review = commands.add_parser("review", help="record decisions about the findings of a report run")
    review.add_argument("--reviewer", required=True, help="your name; saved with every decision")
    review.add_argument("--run", help="a run ID (default: the latest report)")
    review.add_argument("--all", action="store_true", help="also ask about plain facts, not only differences")

    links = commands.add_parser("review-links", help="say how each stored paper relates to its trial (reports results, protocol, mentions only, not this trial)")
    links.add_argument("--reviewer", required=True, help="your name; saved with every answer")
    links.add_argument("nct_ids", nargs="*", help="NCT IDs (default: every stored trial)")

    report = commands.add_parser("report", help="build a report from the stored trials and save it as a run")
    report.add_argument("--as-of", help="the date the time-based check uses, e.g. 2026-11-02 (default: today)")
    report.add_argument("--out-dir", default="outputs/reports", help="folder for the report file")

    ingest = commands.add_parser("ingest", help="download trials from ClinicalTrials.gov and store them")
    ingest.add_argument("nct_ids", nargs="*", help="NCT IDs, for example NCT04644237")
    ingest.add_argument("--file", help="a text file with one NCT ID per line")

    papers = commands.add_parser("ingest-papers", help="find and store PubMed papers for stored trials")
    papers.add_argument("nct_ids", nargs="*", help="NCT IDs (default: every stored trial)")
    papers.add_argument("--file", help="a text file with one NCT ID per line")

    match = commands.add_parser("match-papers", help="LLM: are registered outcomes reported in paper abstracts?")
    match.add_argument("nct_ids", nargs="*", help="NCT IDs")
    match.add_argument("--file", help="a text file with one NCT ID per line")
    match.add_argument("--all", action="store_true", help="every stored trial (calls the model API for each)")
    match.add_argument("--with-baseline", action="store_true", help="also run the optional code reader for diagnostics")
    match.add_argument("--refresh", action="store_true", help="ask the model again even where a stored answer exists")

    safety = commands.add_parser("read-safety", help="LLM: extract safety statements from paper abstracts")
    safety.add_argument("nct_ids", nargs="*", help="NCT IDs")
    safety.add_argument("--file", help="a text file with one NCT ID per line")
    safety.add_argument("--all", action="store_true", help="every stored trial (calls the model API for each abstract)")
    safety.add_argument("--with-baseline", action="store_true", help="also run the optional code reader for diagnostics")
    safety.add_argument("--refresh", action="store_true", help="ask the model again even where a stored answer exists")

    assist = commands.add_parser("suggest-safety", help="LLM match suggestions from stored safety readings; never auto-approve")
    assist.add_argument("nct_ids", nargs="+", help="trial IDs to inspect")
    assist.add_argument("--limit", type=int, default=10, help="maximum new proposal packets (default 10)")
    assist.add_argument("--out-dir", help="packet folder (default TRIALTRACE_SAFETY_MATCH_DIR)")
    assist.add_argument("--prepare-only", action="store_true", help="write evidence packets without calling a model")

    match_review = commands.add_parser("review-safety-match", help="record a human decision on a proposed safety match")
    match_review.add_argument("packet", help="proposal JSON file")
    match_review.add_argument("--reviewer", required=True)
    match_review.add_argument("--decision", required=True, choices=("approved", "rejected", "unclear"))
    match_review.add_argument("--reason", required=True)
    match_review.add_argument("--window", choices=("same", "different", "unknown"), default="unknown")
    match_review.add_argument("--population", choices=("same", "different", "unknown"), default="unknown")

    args = parser.parse_args(argv)
    if args.command == "suggest-safety":
        from trialtrace.safety.safety_assist_cli import suggest_safety
        suggest_safety(read_ids(args.nct_ids, None), args.limit, args.out_dir, args.prepare_only)
    elif args.command == "review-safety-match":
        from trialtrace.safety.safety_assist_cli import review_safety_match
        review_safety_match(args.packet, args.reviewer, args.decision, args.reason, args.window, args.population)
    elif args.command == "ingest":
        run_ingest(args.nct_ids, args.file)
    elif args.command == "ingest-papers":
        run_ingest_papers(args.nct_ids, args.file)
    elif args.command == "match-papers":
        run_match_papers(args.nct_ids, args.file, args.all, args.refresh, args.with_baseline)
    elif args.command == "read-safety":
        run_read_safety(args.nct_ids, args.file, args.all, args.refresh, args.with_baseline)
    elif args.command == "backfill-safety":
        run_backfill_safety()
    elif args.command == "backfill-paper-evidence":
        run_backfill_paper_evidence()
    elif args.command == "rekey-sponsors":
        run_rekey_sponsors()
    elif args.command == "report":
        run_report(args.as_of, args.out_dir)
    elif args.command == "review":
        run_review(args.reviewer, args.run, args.all)
    elif args.command == "review-links":
        run_review_links(args.reviewer, args.nct_ids)


if __name__ == "__main__":
    main()
