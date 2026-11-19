"""Small development check: a few abstracts, live reader, compared with the saved v5 references and the frozen v5 answers.
Development evidence only; the abstracts used here stop being unseen. Usage: python -m evals.dev_check_safety CASE_ID ..."""
import sys
from pathlib import Path

from evals.run_safety_v4 import items, read_json
from evals.safety_v4_reference import pair_statements
from trialtrace.safety.safety_identity import check_reading_identity
from trialtrace.safety.safety_llm import ModelSafetyStatement, read_safety_with_llm
from trialtrace.config import Settings
from trialtrace.paper_reader import reader_settings
from trialtrace.parse.pubmed_parser import Paper

FOLDER = Path("evals/safety_v5")


def tally(reference, statements):
    target = [s for s in reference.statements if s.attribution == "target"]
    pairs = pair_statements(target, statements)
    return len(target), len(pairs), len(statements) - len(pairs)


def main(case_ids):
    data = {i["case"]["case_id"]: i for i in items(FOLDER, require_review=True, check_lock=False)}
    settings = reader_settings(Settings())
    totals = [0, 0, 0, 0, 0]
    for cid in case_ids:
        item = data[cid]
        case, safety = item["case"], item["safety"]
        old = [ModelSafetyStatement(**s) for s in read_json(FOLDER / "run" / (cid + ".json"))["reading"]["statements"]]
        raw = read_safety_with_llm(Paper(pmid=case["pmid"], title=case["title"], abstract=case["abstract"]), settings, target_nct_id=case["nct_id"])
        new = check_reading_identity(raw, case["abstract"], safety, case["registry_title"]).statements
        n, m_old, e_old = tally(item["reference"], old)
        _, m_new, e_new = tally(item["reference"], new)
        print(f"{cid}: reference {n} | frozen run matched {m_old} extras {e_old} | now matched {m_new} extras {e_new}")
        for a, b in zip(totals, (n, m_old, e_old, m_new, e_new)):
            pass
        totals = [t + v for t, v in zip(totals, (n, m_old, e_old, m_new, e_new))]
    print("TOTAL reference %d | frozen matched %d extras %d | now matched %d extras %d" % tuple(totals))


if __name__ == "__main__":
    main(sys.argv[1:])
