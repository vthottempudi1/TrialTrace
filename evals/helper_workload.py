"""Helper accuracy, abstention rate and human work left, from saved answers. No model calls."""
import json
import sys
from pathlib import Path

from evals.run_safety_v4 import items
from evals.safety_v4_reference import pair_statements
from trialtrace.safety.safety_llm import ModelSafetyStatement


def main(folder):
    folder = Path(folder)
    totals = dict.fromkeys(("statements", "proposals", "correct", "abstained", "failed", "manual_left"), 0)
    for item in items(folder, True, False, check_lock=False):
        out = json.loads((folder / "run" / f"{item['case']['case_id']}.json").read_text(encoding="utf-8"))
        accepted = [ModelSafetyStatement(**s) for s in out["reading"]["statements"]]
        target = [s for s in item["reference"].statements if s.attribution == "target"]
        pairs = pair_statements(target, accepted)
        reverse = {si: gi for gi, si in pairs.items()}
        for index, statement in enumerate(accepted):
            totals["statements"] += 1
            packet = out["helpers"].get(str(index))
            proposed = 0
            if packet and packet["status"] == "ready":
                gold = target[reverse[index]] if index in reverse else None
                for name in ("arm", "event"):
                    chosen = packet["proposal"][name]["candidate_id"]
                    if chosen:
                        proposed += 1
                        totals["proposals"] += 1
                        totals["correct"] += bool(gold and chosen in getattr(gold, f"acceptable_{name}_ids"))
            totals["abstained"] += bool(packet and packet["status"] == "abstained")
            totals["failed"] += bool(packet and packet["status"] == "failed")
            needs = 2 if statement.kind == "event" else 1
            totals["manual_left"] += max(needs - proposed, 0)
    p = totals["proposals"]
    print(totals)
    print(f"proposal accuracy {totals['correct']}/{p}; abstentions {totals['abstained']}/{totals['statements']}; "
          f"manual matches left {totals['manual_left']} for {totals['statements']} statements")


if __name__ == "__main__":
    main(sys.argv[1])
