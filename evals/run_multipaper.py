##############################################################################
# evals/run_multipaper.py
#
# PURPOSE: Score the STORED answers of the model and of the code baseline for one trial with several papers
#          against your labels. No model is called: the answers come from `paper_matches` (one match run).
#          Level A (pairs):    every registered outcome x every paper read, M1 to M6 style counts.
#          Level B (outcomes): the roll-up rule per outcome (any identified -> identified; else any unclear ->
#                              unclear; else a failed paper -> unknown; else not identified).
#          Headline (plan, 2026-11-08): an outcome counts as a TRUE POSITIVE only if the system says identified,
#          at least one paper it identified is labelled `yes`, and that paper's quote passes the code check.
#          Truth per outcome: yes if any paper is labelled yes, else unclear if any is unclear, else no.
#          The label file and the case file are hashed into the saved result.
#
# USE:     python -m evals.run_multipaper NCT04644237            (newest match run)
#          python -m evals.run_multipaper NCT04644237 --run match-20261108T030443Z
##############################################################################

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals.prepare_v3 import spans
from trialtrace.compare.abstract_baseline import find_name
from trialtrace.compare.paper_rollup import SUMMARY_LOW_RISK, rollup_statuses, summary_finding
from trialtrace.config import Settings
from trialtrace.db.repository import get_paper_matches

FOLDER = Path(__file__).parent / "multipaper"
RUNS_DIR = Path(__file__).parent / "runs"
ANSWERS = ("yes", "no", "unclear")
ROLES = ("primary", "secondary", "not_stated")
SYSTEMS = ("baseline", "llm")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_labels(path: Path) -> dict[str, dict]:
    """Read labels.txt: `case_id | answer | role | sentences | note`. Anything wrong stops the run."""
    labels = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 4)]
        if len(parts) != 5:
            raise ValueError(f"labels.txt line {line_number}: expected 5 parts separated by |")
        case_id, answer, role, sentences, note = parts
        if answer not in ANSWERS:
            raise ValueError(f"labels.txt line {line_number}: answer must be yes / no / unclear, got {answer!r}")
        if answer == "yes" and role not in ROLES:
            raise ValueError(f"labels.txt line {line_number}: a yes needs a role ({', '.join(ROLES)})")
        if answer == "yes" and not sentences:
            raise ValueError(f"labels.txt line {line_number}: a yes needs the sentence number(s)")
        if answer != "yes" and (role or sentences):
            raise ValueError(f"labels.txt line {line_number}: role and sentences are only for yes")
        numbers = [int(number) for number in sentences.split(",")] if sentences else []
        labels[case_id] = {"answer": answer, "role": role or None, "sentences": numbers, "note": note}
    return labels


def read_cases(path: Path) -> dict[str, dict]:
    cases = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            cases[case["case_id"]] = case
    return cases


def attach_outcome_numbers(rows: list[dict], cases: dict[str, dict]) -> None:
    """Give every stored row its case id by matching role + position + PMID to the cases file."""
    lookup = {}
    for case_id, case in cases.items():
        registered = case["registered"]
        lookup[(registered["role"], registered["position"], case["paper"]["pmid"])] = case_id
    for row in rows:
        row["case_id"] = lookup.get((row["outcome_role"], row["outcome_position"], row["pmid"]))


def quote_is_valid(case: dict, row: dict) -> bool:
    """The quote sits where it says it does and contains the name the system claims."""
    if row["status"] != "identified" or row["quote_text"] is None or not row["matched_name"]:
        return False
    abstract = case["abstract"]
    in_place = abstract[row["quote_start"]:row["quote_end"]] == row["quote_text"]
    has_name = find_name(row["matched_name"], row["quote_text"]) is not None
    return in_place and has_name


def quote_is_labelled_sentence(case: dict, row: dict, label: dict) -> bool:
    """The quote overlaps one of the sentences the labeller picked as reporting the result."""
    if row["quote_start"] is None:
        return False
    sentence_spans = spans(case["abstract"])
    for number in label["sentences"]:
        start, end = sentence_spans[number - 1]
        if row["quote_start"] < end and row["quote_end"] > start:
            return True
    return False


def truth_for_outcome(answers: list[str]) -> str:
    if "yes" in answers:
        return "yes"
    if "unclear" in answers:
        return "unclear"
    return "no"


def score_pairs(rows: list[dict], cases: dict[str, dict], labels: dict[str, dict]) -> dict:
    """Level A: one answer per outcome and paper."""
    result = {"yes": 0, "yes_identified": 0, "yes_ambiguous": 0, "yes_not_identified": 0, "yes_other": 0,
              "no": 0, "no_identified": 0, "no_ambiguous": 0, "no_not_identified": 0, "no_other": 0,
              "unclear": 0, "unclear_identified": 0, "unclear_ambiguous": 0, "unclear_not_identified": 0, "unclear_other": 0,
              "role_correct": 0, "bad_quotes": 0, "quote_is_labelled_sentence": 0, "failures": 0}
    for row in rows:
        case = cases[row["case_id"]]
        label = labels[row["case_id"]]
        answer = label["answer"]
        status = row["status"]
        result[answer] += 1
        if status in ("identified", "ambiguous", "not_identified"):
            result[f"{answer}_{status}"] += 1
        else:
            result[f"{answer}_other"] += 1                  # failed or no_text
        if status == "failed":
            result["failures"] += 1
        if status == "identified" and not quote_is_valid(case, row):
            result["bad_quotes"] += 1
        if answer == "yes" and status == "identified":
            said_role = row["reported_role"] or "not_stated"
            if said_role == label["role"]:
                result["role_correct"] += 1
            if quote_is_labelled_sentence(case, row, label):
                result["quote_is_labelled_sentence"] += 1
    result["m1_correct"] = result["yes_identified"] + result["no_not_identified"]     # right on yes/no pairs
    result["m1_judged"] = result["yes"] + result["no"]
    return result


def score_outcomes(rows: list[dict], cases: dict[str, dict], labels: dict[str, dict]) -> dict:
    """Level B: one answer per registered outcome, by the roll-up rule, and the evidence-supported headline."""
    by_outcome = {}
    for row in rows:
        by_outcome.setdefault((row["outcome_role"], row["outcome_position"]), []).append(row)

    confusion = {}                                  # (truth, system answer) -> how many
    identified_calls = 0
    supported = 0
    plain_true_positives = 0
    truth_yes = 0
    details = []
    for key in sorted(by_outcome, key=lambda k: (k[0] != "primary", k[1])):
        outcome_rows = by_outcome[key]
        truth = truth_for_outcome([labels[row["case_id"]]["answer"] for row in outcome_rows])
        statuses = [row["status"] for row in outcome_rows]
        system_answer = rollup_statuses(statuses, unread=False)
        confusion[(truth, system_answer)] = confusion.get((truth, system_answer), 0) + 1
        if truth == "yes":
            truth_yes += 1
        is_supported = False
        if system_answer == "identified":
            identified_calls += 1
            if truth == "yes":
                plain_true_positives += 1
            for row in outcome_rows:
                if row["status"] == "identified" and labels[row["case_id"]]["answer"] == "yes" and quote_is_valid(cases[row["case_id"]], row):
                    is_supported = True
            if is_supported:
                supported += 1
        details.append({"outcome": f"{key[0]}:{key[1]}", "truth": truth, "system": system_answer, "supported": is_supported})
    return {"outcomes": len(by_outcome), "truth_yes": truth_yes, "identified_calls": identified_calls,
            "supported_true_positives": supported, "plain_true_positives": plain_true_positives,
            "confusion": {f"{truth} -> {answer}": count for (truth, answer), count in sorted(confusion.items())},
            "details": details}


def score_routing(nct_id: str, run_id: str, rows: list[dict], labels: dict[str, dict]) -> dict:
    """Where the model's identified outcomes go: into the review queue, or past it as agreed low-risk findings.

    Uses the real roll-up (summary_finding) on both systems' stored rows. An overclaim is an outcome the model
    called identified although the truth is not yes. 'Missed' = an overclaim that is NOT queued for a person.
    """
    by_outcome = {}
    for row in rows:
        by_outcome.setdefault((row["outcome_role"], row["outcome_position"]), []).append(row)
    no_unread = {"skipped": [], "new": [], "changed": []}
    routing = {"overclaims": 0, "overclaims_queued": 0, "overclaims_missed": 0,
               "true_identified": 0, "true_identified_queued": 0}
    for key in sorted(by_outcome):
        outcome_rows = by_outcome[key]
        llm_rows = [row for row in outcome_rows if row["system"] == "llm"]
        if rollup_statuses([row["status"] for row in llm_rows], unread=False) != "identified":
            continue
        truth = truth_for_outcome([labels[row["case_id"]]["answer"] for row in llm_rows])
        finding = summary_finding(nct_id, outcome_rows, no_unread, run_id)
        queued = finding.category not in SUMMARY_LOW_RISK and finding.category != "unknown"
        if truth == "yes":
            routing["true_identified"] += 1
            routing["true_identified_queued"] += int(queued)
        else:
            routing["overclaims"] += 1
            routing["overclaims_queued" if queued else "overclaims_missed"] += 1
    return routing


def percent(top: int, bottom: int) -> str:
    """top/bottom (share%) with the 95% Wilson interval, e.g. 6/10 (60%, 95% CI 31-83%)."""
    if bottom == 0:
        return "n/a"
    z = 1.96
    share = top / bottom
    centre = (share + z * z / (2 * bottom)) / (1 + z * z / bottom)
    spread = z * ((share * (1 - share) / bottom + z * z / (4 * bottom * bottom)) ** 0.5) / (1 + z * z / bottom)
    return f"{top}/{bottom} ({100 * share:.0f}%, 95% CI {100 * max(0, centre - spread):.0f}-{100 * min(1, centre + spread):.0f}%)"


def show(system: str, pairs: dict, outcomes: dict) -> None:
    print(f"\n=== {system} ===")
    print("Level A, pairs (outcome x paper)")
    print(f"  M1 identification (yes/no pairs)  {percent(pairs['m1_correct'], pairs['m1_judged'])}")
    print(f"  yes pairs: identified {pairs['yes_identified']}, unclear {pairs['yes_ambiguous']}, not identified {pairs['yes_not_identified']}, failed/no text {pairs['yes_other']}   (of {pairs['yes']})")
    print(f"  no pairs: false identified {pairs['no_identified']}, unclear {pairs['no_ambiguous']}   (of {pairs['no']})")
    print(f"  unclear pairs: identified {pairs['unclear_identified']}, unclear {pairs['unclear_ambiguous']}, not identified {pairs['unclear_not_identified']}   (of {pairs['unclear']})")
    print(f"  role correct among identified yes  {percent(pairs['role_correct'], pairs['yes_identified'])}")
    print(f"  quote is the labelled sentence     {percent(pairs['quote_is_labelled_sentence'], pairs['yes_identified'])}")
    print(f"  bad quotes {pairs['bad_quotes']}, failures {pairs['failures']}")
    print("Level B, outcomes (roll-up over the papers read)")
    print(f"  outcomes {outcomes['outcomes']}, truth yes {outcomes['truth_yes']}, system identified {outcomes['identified_calls']}")
    print(f"  HEADLINE evidence-supported: precision {percent(outcomes['supported_true_positives'], outcomes['identified_calls'])}, recall {percent(outcomes['supported_true_positives'], outcomes['truth_yes'])}")
    print(f"  plain any-paper: precision {percent(outcomes['plain_true_positives'], outcomes['identified_calls'])}, recall {percent(outcomes['plain_true_positives'], outcomes['truth_yes'])}")
    print("  truth -> system: " + ", ".join(f"{name} {count}" for name, count in outcomes["confusion"].items()))
    if "routing" in outcomes:
        routing = outcomes["routing"]
        print(f"  ROUTING of identified outcomes: overclaims {routing['overclaims']} (sent to review {routing['overclaims_queued']}, MISSED {routing['overclaims_missed']}); "
              f"correct ones sent to review {routing['true_identified_queued']}/{routing['true_identified']}")


def score_trial(conn, nct_id: str, run_id: str | None = None) -> dict:
    """Score one trial from its stored answers. Returns the results, the run used and the file hashes."""
    folder = FOLDER / nct_id
    labels = read_labels(folder / "labels.txt")
    cases = read_cases(folder / "cases.unlabelled.jsonl")
    if set(labels) != set(cases):
        raise SystemExit(f"{nct_id}: labels and cases differ: missing labels {sorted(set(cases) - set(labels))}, unknown labels {sorted(set(labels) - set(cases))}")
    stored = get_paper_matches(conn, nct_id, run_id)
    if not stored:
        raise SystemExit(f"{nct_id}: no stored match answers")

    results = {}
    all_rows = {}
    for system in SYSTEMS:
        rows = [dict(row) for row in stored if row["system"] == system]
        attach_outcome_numbers(rows, cases)
        rows = [row for row in rows if row["case_id"] is not None]      # only pairs that were labelled
        if len(rows) != len(cases):
            raise SystemExit(f"{nct_id} {system}: {len(rows)} stored pairs match the {len(cases)} labelled cases; the match run read different papers or outcomes")
        results[system] = {"pairs": score_pairs(rows, cases, labels), "outcomes": score_outcomes(rows, cases, labels)}
        all_rows[system] = rows
    results["llm"]["outcomes"]["routing"] = score_routing(nct_id, stored[0]["match_run_id"], all_rows["baseline"] + all_rows["llm"], labels)
    return {"nct_id": nct_id, "match_run_id": stored[0]["match_run_id"], "results": results,
            "labels_sha256": sha256_of(folder / "labels.txt"), "cases_sha256": sha256_of(folder / "cases.unlabelled.jsonl")}


def is_fully_labelled(nct_id: str) -> bool:
    """A trial is scored in --all only when no line of its label file still has a '?'."""
    text = (FOLDER / nct_id / "labels.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("m") and " | ? |" in line:
            return False
    return True


def add_up(parts: list[dict]) -> dict:
    """Sum the counts of several trials (micro-average: every pair and every outcome counts once)."""
    total = {}
    for part in parts:
        for key, value in part.items():
            if key == "details":
                continue
            if isinstance(value, dict):                     # confusion table, routing counts: add key by key
                merged = total.setdefault(key, {})
                for name, count in value.items():
                    merged[name] = merged.get(name, 0) + count
            else:
                total[key] = total.get(key, 0) + value
    return total


def save_result(name: str, content: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved = RUNS_DIR / f"multipaper-{name}-{stamp}.json"
    content = {"scored_at": stamp, "labeller": "single annotator (project owner)", **content}
    saved.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")
    return saved


def main() -> None:
    global FOLDER
    parser = argparse.ArgumentParser()
    parser.add_argument("nct_id", nargs="?", help="one trial (omit with --all)")
    parser.add_argument("--run", help="a match run ID (default: the newest of each trial)")
    parser.add_argument("--all", action="store_true", help="every fully labelled trial in the folder, with totals")
    parser.add_argument("--folder", default=str(FOLDER), help="folder with one sub-folder per trial (default: evals/multipaper)")
    parser.add_argument("--name", default="all", help="name used in the saved result file")
    args = parser.parse_args()
    FOLDER = Path(args.folder)
    if not args.all and not args.nct_id:
        parser.error("give an NCT ID or --all")

    nct_ids = [args.nct_id]
    if args.all:
        nct_ids = sorted(path.name for path in FOLDER.iterdir() if path.is_dir() and is_fully_labelled(path.name))
        waiting = sorted(path.name for path in FOLDER.iterdir() if path.is_dir() and not is_fully_labelled(path.name))
        if waiting:
            print(f"Not scored (labels not finished): {', '.join(waiting)}")

    scored = []
    with psycopg.connect(Settings().database_url) as conn:
        for nct_id in nct_ids:
            scored.append(score_trial(conn, nct_id, args.run))

    for trial in scored:
        print(f"\n##### {trial['nct_id']} (match run {trial['match_run_id']})")
        for system in SYSTEMS:
            show(system, trial["results"][system]["pairs"], trial["results"][system]["outcomes"])

    if args.all:
        print(f"\n##### TOTAL over {len(scored)} trials: {', '.join(t['nct_id'] for t in scored)}")
        totals = {}
        for system in SYSTEMS:
            pairs = add_up([t["results"][system]["pairs"] for t in scored])
            outcomes = add_up([t["results"][system]["outcomes"] for t in scored])
            totals[system] = {"pairs": pairs, "outcomes": outcomes}
            show(system, pairs, outcomes)
        saved = save_result(args.name, {"trials": scored, "totals": totals})
    else:
        saved = save_result(args.nct_id, scored[0])
    print(f"\nSaved {saved}")


if __name__ == "__main__":
    main()
