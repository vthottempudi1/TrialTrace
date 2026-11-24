##############################################################################
# trialtrace/evidence_expansion/eval/run_eval.py
#
# PURPOSE: Run the three configurations on the same cases and score them:
#            v1        = the current abstract-only status (no new work)
#            baseline  = fixed full-text and companion expansion (no agent)
#            agent     = the escalation ladder
#          Cases: the locked test rows of the triage sheet (+ dev rows reported separately)
#          and a few control outcomes that V1 already got right.
#
# USE:     python -m trialtrace.evidence_expansion.eval.run_eval
##############################################################################

import json

import httpx
import psycopg

from trialtrace.compare.abstract_llm import match_with_llm
from trialtrace.config import Settings
from trialtrace.evidence_expansion.agent.agent_ladder import resolve_outcome
from trialtrace.evidence_expansion.agent.budget import Budget
from trialtrace.evidence_expansion.baseline.expand_outcome import Tools, expand_outcome, quote_is_in_source
from trialtrace.evidence_expansion.eval.load_cases import load_case, read_sheet
from trialtrace.evidence_expansion.eval.metrics import regression_on_controls, score
from trialtrace.evidence_expansion.eval.source_cache import SourceCache

GOLD_PATH = "outputs/triage/gold.json"
RESULTS_PATH = "outputs/triage/eval_results.json"
CONTROL_COUNT = 6


class Meter:
    """Counts calls and tokens for one outcome, and wraps the V1 comparison."""

    def __init__(self, cache: SourceCache, settings: Settings):
        self.cache = cache
        self.settings = settings
        self.fetches = 0
        self.model_calls = 0
        self.tokens = 0

    def get_text(self, pmid):
        return self.cache.get_text(pmid)

    def companions(self, nct_id, title, linked_pmids, only=None):
        return self.cache.get_companions(nct_id, title, linked_pmids, only)

    def search(self, query, linked_pmids):
        return self.cache.search(query, linked_pmids)

    def match(self, outcome, paper):
        result = match_with_llm(outcome, paper, self.settings)
        if result.llm is not None:
            self.tokens += (result.llm.input_tokens or 0) + (result.llm.output_tokens or 0)
        return result

    def count(self, kind):
        if kind == "llm":
            self.model_calls += 1
        else:
            self.fetches += 1


def run_baseline(case, meter: Meter):
    linked_pmids = [paper.pmid for paper in case["linked_papers"]]
    tools = Tools(
        get_text=meter.get_text,
        find_companions=lambda: meter.companions(case["outcome"].nct_id, case["trial_title"], linked_pmids),
        match=meter.match,
        count=meter.count,
    )
    result = expand_outcome(case["outcome"], case["trial_title"], case["linked_papers"], tools)
    return result, meter.fetches + meter.model_calls, meter.tokens


def run_agent(case, meter: Meter, settings: Settings):
    budget = Budget(max_calls=15)
    result = resolve_outcome(settings, case["outcome"], case["trial_title"], case["linked_papers"], meter, budget)
    return result, budget.calls + 1, meter.tokens   # +1 call for the cause question the agent always asks


def quote_still_valid(result, cache: SourceCache) -> bool:
    """Re-check the quote against the saved source text (full text, else any saved abstract)."""
    if result.quote is None:
        return False
    text = cache.get_text(result.pmid) if result.pmid else None
    if text is not None and quote_is_in_source(result.quote, text):
        return True
    for companions in list(cache.data["companions"].values()) + list(cache.data["searches"].values()):
        for item in companions:
            if item["pmid"] == result.pmid and item["abstract"] and quote_is_in_source(result.quote, item["abstract"]):
                return True
    return False


def final_status(result, v1_status: str) -> str:
    """A V1 'identified' is never lowered. Otherwise a readable answer replaces the old status."""
    if v1_status == "identified":
        return v1_status
    if result.status == "unresolved":
        return v1_status
    return result.status


def control_rows(conn: psycopg.Connection) -> list[dict]:
    """A few outcomes V1 already marked identified, in sheet-row form."""
    found = conn.execute(
        "select distinct on (nct_id) nct_id, outcome_position, outcome_role, pmid from paper_matches "
        "where system = 'llm' and status = 'identified' and outcome_role in ('primary', 'secondary') "
        "order by nct_id, outcome_position limit %s", (CONTROL_COUNT,)
    ).fetchall()
    rows = []
    for index, (nct_id, position, role, pmid) in enumerate(found, 1):
        rows.append({"id": f"c{index:02d}", "split": "control", "nct_id": nct_id, "outcome_position": position,
                     "role": role, "v1_status": "identified", "linked_pmids": pmid})
    return rows


def main() -> None:
    settings = Settings()
    gold = {item["id"]: item for item in json.load(open(GOLD_PATH, encoding="utf-8"))}
    rows_by_config = {"v1": [], "baseline": [], "agent": []}
    details = []

    with httpx.Client() as http, psycopg.connect(settings.database_url) as conn:
        cache = SourceCache(http, settings)
        sheet = read_sheet() + control_rows(conn)
        for sheet_row in sheet:
            case_id = sheet_row["id"]
            gold_status = "identified" if case_id.startswith("c") else gold.get(case_id, {}).get("status")
            if gold_status not in ("identified", "ambiguous", "not_identified"):
                continue                                          # no usable gold label for this case
            case = load_case(conn, sheet_row)
            base = {"id": case_id, "split": case["split"], "role": case["role"], "gold": gold_status, "v1": case["v1_status"]}

            baseline_result, baseline_calls, baseline_tokens = run_baseline(case, Meter(cache, settings))
            agent_meter = Meter(cache, settings)
            agent_result, agent_calls, agent_tokens = run_agent(case, agent_meter, settings)

            rows_by_config["v1"].append({**base, "final": case["v1_status"], "quote_ok": False, "calls": 0, "tokens": 0})
            for name, result, calls, tokens in (("baseline", baseline_result, baseline_calls, baseline_tokens),
                                                ("agent", agent_result, agent_calls, agent_tokens)):
                final = final_status(result, case["v1_status"])
                rows_by_config[name].append({**base, "final": final, "quote_ok": quote_still_valid(result, cache),
                                             "calls": calls, "tokens": tokens})
            details.append({**base, "baseline": baseline_result.model_dump(), "agent": agent_result.model_dump(),
                            "baseline_calls": baseline_calls, "agent_calls": agent_calls})
            print(case_id, "gold", gold_status, "| v1", case["v1_status"], "| base", baseline_result.status,
                  baseline_result.evidence_source, "| agent", agent_result.status, agent_result.evidence_source, agent_calls)

    # Score: locked test rows (the headline), then dev rows and controls on their own.
    report = {}
    for name, rows in rows_by_config.items():
        test_rows = [row for row in rows if row["split"] == "locked_test"]
        dev_rows = [row for row in rows if row["split"] == "dev"]
        control = [row for row in rows if row["split"] == "control"]
        report[name] = {"locked_test": score(test_rows), "dev": score(dev_rows),
                        "controls_changed_wrongly": regression_on_controls(control)}
    with open(RESULTS_PATH, "w", encoding="utf-8") as results_file:
        json.dump({"report": report, "details": details}, results_file, indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
