"""Prepare new naturalistic and constructed blind reference packets; no models."""
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from evals import prepare_safety_v2 as legacy
from evals.make_safety_cases import blind_sheet, registry_sheet
from trialtrace.config import Settings
from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction
from trialtrace.safety.safety_llm import safety_reader_info
from trialtrace.paper_reader import reader_settings
from trialtrace.safety.safety_assist import HELPER_VERSION

FOLDER = Path("evals/safety_v4")
SEED = 20261113

RULE = """# Safety v4 reference selection rule

Written before drawing/reading new evaluation abstracts or labelling them.
Use only the 60 trial IDs in ../safety_v4_acquisition/draw.json, a metadata-only
external acquisition excluding all previously stored trials. Retain failures.
Eligibility: posted registry safety, at least one listed clean abstract. Exclude
prior case/sheet PMIDs and identical abstract text from strict scores. Production
selection remains first ten numeric PMIDs, with exposed selections coverage-only.
Sort eligible trial IDs, shuffle once using seed 20261113, take 30; keep all
others in reserve with fixed inputs. No content/performance-based redrawing.
If fewer than 60 strict target-attributable gold statements are labelled, add
the next ten reserve trials (or remaining smaller block), label and repeat.
The initial blind subset is the first 20 strict abstracts after seeded shuffling
of eligible trials, one per trial first, then remaining abstracts if needed.
Venu or a separate reviewer labels that subset BEFORE viewing any AI drafts.
Do not draft labels for that subset. Record actual authorship and review dates.
All model calls require complete reviewed references and a hashed freeze.
The constructed targeted set has 40 fictional cases: ten agreement, ten disagreement and four per other family;
it is reviewed independently, scored separately and is not real-world validation.
No baseline is required. Never simulate human match approvals to improve scores.
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def lock_candidate(folder):
    """Lock the system before labeling; fitting code to new gold invalidates it."""
    paths = list(Path("trialtrace").rglob("*.py")) + list(Path("trialtrace/db/migrations").glob("*.sql"))
    record = {"locked_at": datetime.now(timezone.utc).isoformat(),
              "reader": safety_reader_info(reader_settings(Settings())), "helper_version": HELPER_VERSION,
              "files": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    with (folder / "system_candidate.json").open("x", encoding="utf-8") as out:
        json.dump(record, out, indent=2)
    return record


def registry_text(nct, safety):
    text = registry_sheet(nct, safety) + "\n## Candidate IDs and group populations\n\n"
    for arm in safety.arms:
        text += f"- arm:{arm.group_id}: {arm.title}\n  {arm.description or '(not stated)'}\n"
    for event in safety.events:
        text += f"- event:{event.kind}:{event.position}: {event.term}; {event.notes or ''}\n"
    return text


def write_trial(folder, nct, data):
    folder.mkdir(parents=True, exist_ok=False)
    safety, papers, snapshot = data["safety"], data["selected"], data["snapshot"]
    cases = [{"case_id": f"{nct}-{p['pmid']}", "nct_id": nct, "pmid": p["pmid"],
              "title": p["title"], "abstract": p["abstract"], "paper_raw_hash": p["raw_hash"],
              "abstract_sha256": hashlib.sha256(p["abstract"].encode()).hexdigest(),
              "registry_hash": snapshot.content_hash, "registry_title": snapshot.title,
              "strict_holdout": p["pmid"] in data["strict_holdout_pmids"], "synthetic": False}
             for p in papers]
    write_json(folder / "cases.json", cases)
    write_json(folder / "safety.json", safety.model_dump(mode="json"))
    write_json(folder / "snapshot.json", snapshot.model_dump(mode="json"))
    write_json(folder / "selection.json", {"selected_pmids": [p["pmid"] for p in papers],
        "skipped_pmids": [p["pmid"] for p in data["skipped"]], "paper_limit": 10})
    (folder / "sheet.md").write_text(blind_sheet(nct, papers), encoding="utf-8")
    (folder / "registry.md").write_text(registry_text(nct, safety), encoding="utf-8")
    (folder / "references.jsonl").write_text("".join(json.dumps({"case_id": c["case_id"],
        "document": None, "statements": None, "notes": ""}) + "\n" for c in cases), encoding="utf-8")
    return cases


def challenge_cases():
    """Authored source data only: no answers or expected categories in this packet."""
    definitions = []
    window = "From first dose through day 30."
    for i in range(4):
        definitions.append(("agreement", f"Safety was collected from first dose through day 30 in all treated participants. Serious adverse events occurred in {4+i} of 120 participants in the Drug group.", 4+i, False, []))
    for i in range(4):
        definitions.append(("disagreement", f"Safety was collected from first dose through day 30 in all treated participants. Serious adverse events occurred in {8+i} of 120 participants in the Drug group.", 4+i, False, []))
    populations = [
        "Serious adverse events affected 2 of 40 AYAs receiving Drug; the full treated Drug group contained 120 participants.",
        "In the Drug group, hypertension affected 2 of 40 AYAs and 8 of 80 non-AYAs.",
        "Serious adverse events affected 6 of 120 participants receiving Drug, all of whom received at least one dose.",
        "Serious adverse events affected 2 of 40 participants older than 70 years receiving Drug."]
    definitions.extend(("population", text, 6, False, []) for text in populations)
    identities = [
        ("This open-label study evaluated focal seizures. No cases of DRESS occurred.", True),
        ("This randomized placebo-controlled study has an open-label extension. Serious adverse events affected 6 of 120 participants receiving Drug.", True),
        ("In NCT98009999, serious adverse events affected 6 of 120 participants receiving Drug. The target trial has no safety results reported here.", False),
        ("In {target}, serious adverse events affected 6 of 120 participants receiving Drug. NCT98009998 and NCT98009999 were separate studies with no results reported here.", False)]
    definitions.extend(("identity", text, 6, placebo, []) for text, placebo in identities)
    scopes = [
        "Hypertension affected 10 of 120 participants receiving Drug.",
        "Serious hypertension affected 2 of 120 participants receiving Drug.",
        "Non-serious hypertension affected 8 of 120 participants receiving Drug.",
        "No serious cardiovascular or thrombotic events occurred among participants receiving Drug."]
    definitions.extend(("event_scope", text, 6, False, [("serious", "Hypertension", 2), ("other", "Hypertension", 8)]) for text in scopes)
    measures = [
        "The cumulative incidence of death at day 30 was 10% in the Drug group.",
        "The Kaplan-Meier probability of death at day 30 was 10% in the Drug group.",
        "From first dose through day 30, 12 of 120 treated participants in the Drug group died from any cause.",
        "About 10% of Drug participants died; the risk ratio was 0.8."]
    definitions.extend(("measure", text, 6, False, []) for text in measures)
    granularity = [
        "Six participants receiving Drug died: two from pneumonia and four from disease progression.",
        "No serious cardiovascular or thrombotic events occurred among participants receiving Drug.",
        "Nausea affected 3 participants and vomiting affected 2 participants receiving Drug.",
        "Three Drug participants died, including one from pneumonia; three placebo participants died."]
    definitions.extend(("granularity", text, 6, i == 3, []) for i, text in enumerate(granularity))
    # Ten positive and ten negative numeric examples can reach the declared
    # branch minima after an actual human review exercise.
    for i in range(6):
        definitions.append(("agreement", f"Safety was collected from first dose through day 30 in all treated participants. Serious adverse events occurred in {12+i} of 120 participants in the Drug group.", 12+i, False, []))
        definitions.append(("disagreement", f"Safety was collected from first dose through day 30 in all treated participants. Serious adverse events occurred in {24+i} of 120 participants in the Drug group.", 12+i, False, []))
    output = []
    for i, (family, abstract, serious, placebo, events) in enumerate(definitions, 1):
        nct = f"NCT9904{i:04d}"
        abstract = abstract.replace("{target}", nct)
        version = digest([nct, abstract, serious, placebo, events])
        arms = [SafetyArm(nct_id=nct, content_hash=version, position=0, group_id="EG0", title="Drug",
            description="All 120 participants who received at least one dose of Drug.",
            serious_affected=serious, serious_at_risk=120, deaths_affected=12, deaths_at_risk=120)]
        if placebo:
            arms.append(SafetyArm(nct_id=nct, content_hash=version, position=1, group_id="EG1", title="Placebo",
                description="All 120 participants who received placebo.", serious_affected=6, serious_at_risk=120,
                deaths_affected=3, deaths_at_risk=120))
        parsed_events = []
        for kind, term, count in events:
            position = sum(e.kind == kind for e in parsed_events)
            parsed_events.append(SafetyEvent(nct_id=nct, content_hash=version, position=position,
                kind=kind, term=term, counts=[SafetyEventCount(group_id="EG0", affected=count, at_risk=120)]))
        safety = SafetyExtraction(status="ok", time_frame=window, frequency_threshold=0, arms=arms, events=parsed_events)
        case = {"case_id": f"T{i:02d}", "nct_id": nct, "pmid": str(999400000+i),
                "title": "Constructed safety abstract (fictional identifiers)", "abstract": abstract,
                "paper_raw_hash": digest(abstract), "abstract_sha256": hashlib.sha256(abstract.encode()).hexdigest(),
                "registry_hash": version, "registry_title": "Placebo-controlled PGTC trial" if i == 13 else "Constructed Drug safety trial",
                "strict_holdout": False, "synthetic": True, "family": family}
        output.append((case, safety))
    return output


def main():
    FOLDER.mkdir(exist_ok=False)
    (FOLDER / "RULE.md").write_text(RULE, encoding="utf-8")
    (FOLDER / "protocol.md").write_text(Path("evals/safety_v4_protocol.md").read_text(encoding="utf-8"), encoding="utf-8")
    lock_candidate(FOLDER)
    draw = json.loads(Path("evals/safety_v4_acquisition/draw.json").read_text(encoding="utf-8"))
    legacy.FOLDER = FOLDER  # Count all older packets as prior exposure, including v2/v3.
    with psycopg.connect(Settings().database_url) as conn:
        pool, excluded_pmids, excluded_texts = legacy.pool_from_database(conn)
    pool = {n: d for n, d in pool.items() if n in draw["selected"]}
    order = sorted(pool)
    random.Random(SEED).shuffle(order)
    selected, reserve = order[:30], order[30:]
    if not selected:
        raise ValueError("No eligible fresh trials; retain the acquisition record and extend prospectively")
    all_cases = []
    for n in selected:
        all_cases.extend(write_trial(FOLDER / "naturalistic" / n, n, pool[n]))
    reserve_inputs = {n: {"snapshot": pool[n]["snapshot"].model_dump(mode="json"),
        "safety": pool[n]["safety"].model_dump(mode="json"), "selected": pool[n]["selected"],
        "skipped": pool[n]["skipped"], "strict_holdout_pmids": pool[n]["strict_holdout_pmids"]} for n in reserve}
    write_json(FOLDER / "reserve_inputs.json", reserve_inputs)
    write_json(FOLDER / "draw.json", {"seed": SEED, "selected": selected, "reserve": reserve})
    challenge = challenge_cases()
    for case, safety in challenge:
        d = FOLDER / "targeted" / case["case_id"]
        d.mkdir(parents=True)
        write_json(d / "cases.json", [case])
        write_json(d / "safety.json", safety.model_dump(mode="json"))
        (d / "sheet.md").write_text(blind_sheet(case["nct_id"], [case]), encoding="utf-8")
        (d / "registry.md").write_text("# CONSTRUCTED — not a real registry record\n\n" + registry_text(case["nct_id"], safety), encoding="utf-8")
        (d / "references.jsonl").write_text(json.dumps({"case_id": case["case_id"], "document": None,
            "statements": None, "notes": ""}) + "\n", encoding="utf-8")
    strict = [c for c in all_cases if c["strict_holdout"]]
    first_per_trial = [next(c for c in strict if c["nct_id"] == n) for n in selected if any(c["nct_id"] == n for c in strict)]
    random.Random(SEED).shuffle(first_per_trial)
    blind = first_per_trial[:20]
    write_json(FOLDER / "blind_subset.json", {"case_ids": [c["case_id"] for c in blind],
        "instruction": "Independently label these cases before viewing any AI draft references. None have been drafted."})
    blind_text = "# Independently label these 20 abstracts first\n\nNo AI reference labels or model answers are included. Read protocol.md.\n\n"
    for c in blind:
        n = c["nct_id"]
        blind_text += f"## {c['case_id']}\n\n[Registry](naturalistic/{n}/registry.md) · [Fill references](naturalistic/{n}/references.jsonl)\n\n"
        blind_text += blind_sheet(n, [c]) + "\n"
    (FOLDER / "BLIND_SUBSET.md").write_text(blind_text, encoding="utf-8")
    write_json(FOLDER / "reference_review.json", {"reviewer": None, "completed": False, "reviewed_at": None,
        "blind_subset_completed": False, "blind_subset_labelled_before_ai_drafts": False,
        "blind_subset_reviewer": None, "blind_subset_reviewed_at": None, "notes": ""})
    input_hashes = {str(p.relative_to(FOLDER)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in FOLDER.rglob("*") if p.is_file() and p.name in (
                        "cases.json", "safety.json", "snapshot.json", "selection.json", "sheet.md", "registry.md", "system_candidate.json",
                        "reserve_inputs.json", "draw.json", "blind_subset.json", "BLIND_SUBSET.md", "RULE.md", "protocol.md")}
    write_json(FOLDER / "manifest.json", {"prepared_at": datetime.now(timezone.utc).isoformat(), "seed": SEED,
        "selected_trials": selected, "reserve_trials": reserve, "naturalistic_cases": len(all_cases),
        "strict_unseen_cases": len(strict), "targeted_cases": len(challenge),
        "excluded_prior_pmids": excluded_pmids, "excluded_prior_texts": excluded_texts,
        "input_hashes": input_hashes,
        "status": "Blank references; actual independent review outstanding; no model called"})
    index = "# Safety v4 reference review\n\nStart with [the independent blind subset](BLIND_SUBSET.md) and [protocol.md](protocol.md). All references are blank.\n\n| Set/trial | Abstracts | Sources | Registry | Reference file |\n|---|---:|---|---|---|\n"
    for n in selected:
        count = sum(c["nct_id"] == n for c in all_cases)
        index += f"| Naturalistic {n} | {count} | [read](naturalistic/{n}/sheet.md) | [read](naturalistic/{n}/registry.md) | [fill](naturalistic/{n}/references.jsonl) |\n"
    for c, _ in challenge:
        cid = c["case_id"]
        index += f"| Constructed {cid} | 1 | [read](targeted/{cid}/sheet.md) | [read](targeted/{cid}/registry.md) | [fill](targeted/{cid}/references.jsonl) |\n"
    (FOLDER / "REVIEW_INDEX.md").write_text(index, encoding="utf-8")
    (FOLDER / "START_HERE.md").write_text(
        "# Start safety v4 review\n\nFirst independently label [the 20-case blind subset](BLIND_SUBSET.md) "
        "using [protocol.md](protocol.md), before viewing any AI drafts. All reference files are blank.\n\n"
        "Then complete [the full review index](REVIEW_INDEX.md): naturalistic and constructed cases. "
        "Use statements=[] for an empty source inventory; null means not labelled. See the syntax example in protocol.md. "
        "Record actual review only after completing it.\n\n"
        "Validate: `python -m evals.run_safety_v4 --check-labels`. No model call before review and freeze.\n",
        encoding="utf-8")
    print(json.dumps({"trials": len(selected), "naturalistic_abstracts": len(all_cases),
        "strict_unseen_cases": len(strict), "reserve_trials": len(reserve), "constructed_cases": len(challenge)}))


if __name__ == "__main__":
    main()
