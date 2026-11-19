"""Build a review sheet of saved v5 helper event proposals whose labels name no registry row. No model calls."""
import json
from pathlib import Path

from evals.run_safety_v4 import items
from evals.safety_v4_reference import pair_statements
from trialtrace.safety.safety_llm import ModelSafetyStatement

FOLDER = Path("evals/safety_v5")


def main():
    rows = []
    for item in items(FOLDER, True, False, check_lock=False):
        case_id = item["case"]["case_id"]
        out = json.loads((FOLDER / "run" / f"{case_id}.json").read_text(encoding="utf-8"))
        accepted = [ModelSafetyStatement(**s) for s in out["reading"]["statements"]]
        target = [s for s in item["reference"].statements if s.attribution == "target"]
        pairs = pair_statements(target, accepted)
        reverse = {si: gi for gi, si in pairs.items()}
        for key, packet in out["helpers"].items():
            si = int(key)
            if packet["status"] != "ready":
                continue
            chosen = packet["proposal"]["event"]["candidate_id"]
            gold = target[reverse[si]] if si in reverse else None
            if not chosen or (gold and chosen in gold.acceptable_event_ids):
                continue
            row = next(c for c in packet["candidates"] if c["id"] == chosen)
            rows.append((case_id, accepted[si], row, packet["proposal"]["event"]))
    lines = ["# Helper event proposals to review (v5)", "",
             "Each row: the abstract statement, and the registry row the helper proposed. Mark Right only if the registry row",
             "counts the same event, in the same seriousness group. Write Wrong if it is narrower, broader or a different event.", ""]
    for number, (case_id, st, row, match) in enumerate(rows, 1):
        stratum = "serious events table" if row["seriousness"] == "serious" else "other (non-serious) events table"
        lines += [f"## {number}. {case_id}", "",
                  f"- Abstract: **{st.term}**, arm {st.arm or 'not stated'}, scope {st.event_scope}",
                  f"- Quote: {match['abstract_quote']}",
                  f"- Proposed registry row: **{row['title']}** ({stratum}, {row['id']})",
                  "- Decision (Right / Wrong): ", "- Note: ", ""]
    out_path = FOLDER / "HELPER_REVIEW.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(len(rows), "proposals written to", out_path)


if __name__ == "__main__":
    main()
