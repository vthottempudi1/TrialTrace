##############################################################################
# evals/run.py
#
# PURPOSE: Score a system against the labelled cases, using exactly the metrics in
#          evals/rubric.md, and save the full outputs of the run (rubric section 7).
#          Today the only system is the code-only baseline; the LLM version plugs in later.
#          Held-out cases are NOT scored unless you pass --final: they are used once, at the end.
#
# A SYSTEM'S ANSWER for one case is a dict:
#     status         identified | not_identified | ambiguous | no_text
#     reported_role  primary | secondary | None
#     matched_name   the name it found, or None
#     quote          {text, start, end} or None
#
# METRICS (rubric section 4), computed per split:
#   M1 identification accuracy   cases labelled yes/no: identified for yes, not_identified for no
#   M2 role accuracy             yes cases: identified AND the role equals the label (not_stated = no cue)
#   M3 bad quotes                an `identified` whose quote is not at its place or does not name the outcome.
#                                NOTE: 0 means the quote is real and names the outcome, NOT that the sentence
#                                reports its result (that is what M3b and a person's review look at)
#   M3b quote agreement          yes cases: the system's quote is the sentence the labeller picked (reported, not in the keep rule)
#   M4 false identified          system says identified, label says no
#   M5 unclear cases             what the system said on cases labelled unclear (reported, never rewarded)
#   M6 failures                  cases where the system raised an error
#
# USE:     python -m evals.run                 (baseline, dev cases)
#          python -m evals.run --final         (baseline, dev AND held-out: only at the end)
##############################################################################

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from evals.label import CASES_FILE, load_cases
from trialtrace.compare.abstract_baseline import TextQuote, find_name, match_one, quote_is_in_text
from trialtrace.compare.abstract_llm import PROMPT, match_with_llm
from trialtrace.config import Settings
from trialtrace.contracts import RegisteredOutcome
from trialtrace.parse.pubmed_parser import Paper

RUNS_DIR = Path(__file__).parent / "runs"


def outcome_and_paper(case: dict) -> tuple[RegisteredOutcome, Paper]:
    """Rebuild the registered outcome and the paper from the frozen case."""
    registered = case["registered"]
    outcome = RegisteredOutcome(
        nct_id=case["nct_id"],
        content_hash=case["registry_content_hash"],
        role=registered["role"],
        position=registered["position"],
        measure_text=registered["measure_text"],
        time_frame=registered["time_frame"],
    )
    paper = Paper(pmid=case["paper"]["pmid"], abstract=case["abstract"], listed_nct_ids=[case["nct_id"]])
    return outcome, paper


def llm_answer(case: dict, settings: Settings, client=None) -> dict:
    """What the LLM version says for one case. A failed model answer is shown as status `failed`."""
    outcome, paper = outcome_and_paper(case)
    match = match_with_llm(outcome, paper, settings, client=client)
    quote = None
    if match.quote is not None:
        quote = match.quote.model_dump()
    answer = {
        "status": match.status,
        "reported_role": match.reported_role,
        "matched_name": match.matched_name,
        "quote": quote,
    }
    if match.status == "failed":
        answer["error"] = match.error_code                  # counted in M6
    if match.llm is not None:
        answer["llm"] = match.llm.model_dump()              # model, raw text, tokens, seconds, attempts
    return answer


def baseline_answer(case: dict) -> dict:
    """What the code-only baseline says for one case."""
    outcome, paper = outcome_and_paper(case)
    match = match_one(outcome, paper)
    quote = None
    if match.quote is not None:
        quote = match.quote.model_dump()
    return {
        "status": match.status,
        "reported_role": match.reported_role,
        "matched_name": match.matched_name,
        "quote": quote,
    }


def is_unsupported(case: dict, answer: dict) -> bool:
    """An `identified` answer whose quote is not really in the abstract, or does not contain the name it claims."""
    if answer["status"] != "identified":
        return False
    quote = answer["quote"]
    if quote is None or not answer["matched_name"]:
        return True
    paper = Paper(pmid="0", abstract=case["abstract"])
    quote_has_correct_position = quote_is_in_text(TextQuote(**quote), paper)
    quote_contains_name = find_name(answer["matched_name"], quote["text"]) is not None
    return not quote_has_correct_position or not quote_contains_name


def quote_agrees(case: dict, answer: dict) -> bool:
    """The system said `identified` and its quote is the very sentence the labeller picked as the one that reports it."""
    labelled = case["label"].get("quote")
    quote = answer["quote"]
    if answer["status"] != "identified" or labelled is None or quote is None:
        return False
    same_start = quote["start"] == labelled["start"]
    same_end = quote["end"] == labelled["end"]
    return same_start and same_end


def score(cases: list[dict], answers: dict[str, dict]) -> dict:
    """The rubric's metrics for one set of cases. Every case must be labelled."""
    unlabelled = []
    for case in cases:
        if case["label"] is None:
            unlabelled.append(case["case_id"])
    if unlabelled:
        raise ValueError(f"unlabelled cases: {unlabelled}")

    judged_count = 0
    yes_count = 0
    correct_identifications = 0
    correct_roles = 0
    bad_quotes = 0
    agreeing_quotes = 0
    false_identifications = 0
    overclaims_on_unclear = 0
    true_positives = 0
    identified_total = 0
    unclear_answers = {}
    failures = 0

    # Compare each system answer with the person's saved label.
    for case in cases:
        label = case["label"]
        answer = answers[case["case_id"]]
        reported = label["reported"]
        status = answer["status"]

        # M1: unclear labels are reported separately, not marked right or wrong.
        if reported in ("yes", "no"):
            judged_count += 1
            if reported == "yes":
                expected_status = "identified"
            else:
                expected_status = "not_identified"
            if status == expected_status:
                correct_identifications += 1

        # M2 and M3b: only yes labels have an expected role and quote.
        if reported == "yes":
            yes_count += 1
            expected_role = label["reported_role"]
            if expected_role == "not_stated":
                expected_role = None
            if status == "identified" and answer["reported_role"] == expected_role:
                correct_roles += 1
            if quote_agrees(case, answer):
                agreeing_quotes += 1

        # Recall and precision: a true positive is `identified` on a yes case.
        if status == "identified":
            identified_total += 1
            if reported == "yes":
                true_positives += 1

        # M3 checks quote placement and the matching name, not clinical meaning.
        if is_unsupported(case, answer):
            bad_quotes += 1

        # M4: claiming an outcome was found when the label says no.
        if status == "identified" and reported == "no":
            false_identifications += 1

        # M5: count each type of answer on unclear cases. M4b: "identified" there is overclaiming.
        if reported == "unclear":
            if status == "identified":
                overclaims_on_unclear += 1
            unclear_answers[status] = unclear_answers.get(status, 0) + 1

        # M6: errors stay in the results rather than being dropped.
        if answer.get("error"):
            failures += 1

    # Recall = found / all yes cases. Precision = found / everything said `identified` (no and unclear both count as wrong).
    recall = None
    if yes_count > 0:
        recall = true_positives / yes_count
    precision = None
    if identified_total > 0:
        precision = true_positives / identified_total
    f1 = None
    if recall is not None and precision is not None and (recall + precision) > 0:
        f1 = 2 * recall * precision / (recall + precision)

    return {
        "cases": len(cases),
        "M1_identification": {"correct": correct_identifications, "of": judged_count},
        "M2_role": {"correct": correct_roles, "of": yes_count},
        "M3_bad_quotes": bad_quotes,
        "M3b_quote_agreement": {"correct": agreeing_quotes, "of": yes_count},
        "M4_false_identified": false_identifications,
        "M4b_overclaim_on_unclear": overclaims_on_unclear,
        "M5_on_unclear_cases": unclear_answers,
        "M6_failures": failures,
        "recall": {"correct": true_positives, "of": yes_count, "value": recall},
        "precision": {"correct": true_positives, "of": identified_total, "value": precision},
        "f1": f1,
    }


def usage_of(answers: dict[str, dict]) -> dict:
    """M7: tokens and time spent on the model calls (the baseline has none)."""
    input_tokens = 0
    output_tokens = 0
    seconds = 0.0
    calls = 0
    for answer in answers.values():
        llm = answer.get("llm")
        if llm is None:
            continue
        calls += 1
        input_tokens += llm["input_tokens"] or 0
        output_tokens += llm["output_tokens"] or 0
        seconds += llm["seconds"]
    return {"calls": calls, "input_tokens": input_tokens, "output_tokens": output_tokens, "seconds": round(seconds, 2)}


def run(system: str, final: bool, settings: Settings | None = None, client=None, cases_path: Path = CASES_FILE) -> dict:
    if settings is None:
        settings = Settings()
    cases = load_cases(cases_path)
    # Development cases are the default. Final runs also include held-out cases.
    splits = ["dev"]
    if final:
        splits.append("held_out")
    results = {}
    for split in splits:
        chosen = []
        for case in cases:
            if case["split"] == split:
                chosen.append(case)
        answers = {}
        for case in chosen:
            try:
                if system == "llm":
                    answers[case["case_id"]] = llm_answer(case, settings, client)
                else:
                    answers[case["case_id"]] = baseline_answer(case)
            except Exception as error:                      # a failure is counted and shown, never dropped
                answers[case["case_id"]] = {
                    "status": "failed",
                    "reported_role": None,
                    "matched_name": None,
                    "quote": None,
                    "error": type(error).__name__,
                }
        results[split] = {"scores": score(chosen, answers), "usage": usage_of(answers), "answers": answers}
    return results


def percent(value: float | None) -> str:
    if value is None:
        return "not defined"
    return f"{value * 100:.0f}%"


def show(results: dict, cases: list[dict]) -> None:
    for split, result in results.items():
        s = result["scores"]
        print(f"\n== {split} ({s['cases']} cases) ==")
        print(f"M1 identification accuracy : {s['M1_identification']['correct']}/{s['M1_identification']['of']}  (primary metric)")
        print(f"M2 role accuracy           : {s['M2_role']['correct']}/{s['M2_role']['of']}")
        print(f"M3 bad quotes              : {s['M3_bad_quotes']}  (must be 0; real quote naming the outcome, not proof it reports the result)")
        print(f"M3b quote agreement        : {s['M3b_quote_agreement']['correct']}/{s['M3b_quote_agreement']['of']}  (reported, not in the keep rule)")
        print(f"M4 false 'identified'      : {s['M4_false_identified']}")
        print(f"M4b overclaim on unclear   : {s['M4b_overclaim_on_unclear']}  (identified where the label is unclear)")
        print(f"M5 on 'unclear' cases      : {s['M5_on_unclear_cases']}")
        print(f"M6 failures                : {s['M6_failures']}")
        print(f"Recall                     : {s['recall']['correct']}/{s['recall']['of']}  ({percent(s['recall']['value'])})")
        print(f"Precision                  : {s['precision']['correct']}/{s['precision']['of']}  ({percent(s['precision']['value'])})")
        print(f"F1                         : {percent(s['f1'])}")
        usage = result["usage"]
        if usage["calls"] > 0:
            print(f"M7 cost                    : {usage['calls']} calls, {usage['input_tokens']} input + {usage['output_tokens']} output tokens, {usage['seconds']} s")
        print("\ncase  label     role         system          system role")
        for case in cases:
            if case["split"] != split:
                continue
            answer = result["answers"][case["case_id"]]
            label = case["label"]
            labelled_role = label["reported_role"] or "-"
            system_role = answer["reported_role"] or "-"
            print(f"{case['case_id']}   {label['reported']:<8}  {labelled_role:<11}  {answer['status']:<14}  {system_role}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals.run")
    parser.add_argument("--system", choices=["baseline", "llm"], default="baseline", help="which system to score")
    parser.add_argument("--model", help="the OpenAI model for the LLM system (default: OPENAI_CHAT_MODEL)")
    parser.add_argument("--temperature", default="0", help="0, or 'none' to leave it out (newer models reject 0)")
    parser.add_argument("--cases", default=str(CASES_FILE), help="the cases file (default: the v1 cases)")
    parser.add_argument("--final", action="store_true", help="also score the held-out cases (use once, at the end)")
    args = parser.parse_args()
    settings = Settings()
    if args.model:
        settings.model_name = args.model
    if args.temperature.lower() == "none":
        settings.llm_temperature = None
    else:
        settings.llm_temperature = float(args.temperature)
    cases_path = Path(args.cases)
    results = run(args.system, args.final, settings, cases_path=cases_path)
    cases = load_cases(cases_path)
    show(results, cases)

    RUNS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.final:
        run_type = "final"
    else:
        run_type = "dev"
    cases_name = "" if cases_path == CASES_FILE else f"{cases_path.parent.name}-"
    model_part = ""
    if args.system == "llm":
        model_part = f"{settings.model_name}-"                  # keeps the runs of different models apart
    path = RUNS_DIR / f"{args.system}-{model_part}{cases_name}{timestamp}-{run_type}.json"
    saved_run = {
        "system": args.system,
        "final": args.final,
        "results": results,
    }
    if args.system == "llm":
        # Rubric section 7: keep the model, its settings and the prompt with the outputs.
        saved_run["model"] = settings.model_name
        saved_run["temperature"] = settings.llm_temperature
        saved_run["prompt_template"] = PROMPT
    file_text = json.dumps(saved_run, indent=1, ensure_ascii=False)
    path.write_text(file_text, encoding="utf-8")
    print(f"\nFull outputs saved to {path}")
    print("Limits: 15 cases is a first experiment; labels are owner-adopted, single-source; abstract only.")


if __name__ == "__main__":
    main()
