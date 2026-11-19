"""Prepare a class-balanced, provisional benchmark from real stored records.

Run from TRIALTRACE: python -m evals.prepare_v3 export|screen|build|check
Screening proposes labels; it does not run either system under evaluation.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re

import psycopg

from trialtrace.config import Settings
from trialtrace.db.repository import get_latest_snapshot, get_outcomes, get_papers_for_trial
from trialtrace.llm import call_llm

ROOT = Path(__file__).parent / "v3"
SEED = 20261107
QUOTAS = {"dev": {"yes": 40, "no": 24, "unclear": 16},
          "held_out": {"yes": 10, "no": 6, "unclear": 4}}
PROMPT = """You are proposing provisional evaluation labels from source text, not running a matcher.
Use ONLY this abstract and the supplied registered outcomes. Do not use external knowledge.
For EACH outcome return reported=yes/no/unclear, role=primary/secondary/not_stated (only for yes),
sentence=the numbered RESULT sentence (only for yes), reason, and uncertainty=low/medium/high.
yes: this trial actually reports a matching outcome result, qualitative or numerical.
no: absent, planned only, background only, or result belongs to a different trial.
unclear: related result exists but measure, denominator, population, subgroup or time window differs
or cannot be established. Overall results do not establish subgroup results. Broad safety statements
do not establish specific AE percentages. A generic 'all outcomes improved' without a specific result
is insufficient for a confident yes. Explicit null results count as reports. Do not infer role from
registry role: use abstract endpoint-role statements. Copy no invented facts. A yes must cite a result,
not just a methods sentence listing an endpoint. Roles may be established in another sentence.
Return JSON object {{"labels":[{{"index":0,"reported":"yes","role":"primary",
"sentence":4,"reason":"...","uncertainty":"low"}}]}}. Include every outcome index exactly once.

Trial: {nct_id}
Paper title: {title}
Numbered abstract:
{abstract}
Registered outcomes (index, title, time frame, description):
{outcomes}
"""


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def spans(text):
    """Keep original offsets, and avoid splitting after vs., e.g., and i.e."""
    start = 0
    result = []
    for gap in re.finditer(r"(?<=[.!?])\s+", text):
        prefix = text[start:gap.start()]
        if re.search(r"\b(?:vs|e\.g|i\.e|s\.d|s\.e|a\.m|p\.m|Dr|Mr|Mrs)\.$", prefix, re.IGNORECASE):
            continue
        result.append((start, gap.start()))
        start = gap.end()
    if start < len(text):
        result.append((start, len(text)))
    return result


def export():
    used = set()
    for path in [ROOT.parent / "cases.jsonl", ROOT.parent / "v2/cases.jsonl"]:
        used.update(case["nct_id"] for case in read_jsonl(path))
    candidates = []
    with psycopg.connect(Settings().database_url) as conn:
        ids = conn.execute("SELECT DISTINCT nct_id FROM snapshots ORDER BY nct_id").fetchall()
        for (nct_id,) in ids:
            if nct_id in used:
                continue
            snapshot = get_latest_snapshot(conn, nct_id)
            extraction = get_outcomes(conn, nct_id, snapshot.content_hash)
            papers = [p for p in get_papers_for_trial(conn, nct_id)
                      if p["status"] == "listed" and p["abstract"]]
            if not extraction or not extraction.outcomes or not papers:
                continue
            # Prefer original result articles over protocols/reviews; no system scores used.
            def paper_priority(paper):
                title = paper["title"] or ""
                not_original = bool(re.search(r"protocol|study design|meta-analysis|systematic review", title, re.I)
                                    or re.search(r"Cochrane|review included|pooled analysis", paper["abstract"], re.I))
                return (not_original, int(paper["pmid"]))
            paper = sorted(papers, key=paper_priority)[0]
            candidates.append({"nct_id": nct_id, "snapshot": snapshot.model_dump(mode="json"),
                               "outcomes": [o.model_dump() for o in extraction.outcomes], "paper": paper})
    write_json(ROOT / "candidates.json", candidates)
    print(f"Exported {len(candidates)} new trials, {sum(len(t['outcomes']) for t in candidates)} outcomes", flush=True)


def screen_trial(trial):
    path = ROOT / "screening" / f"{trial['nct_id']}.json"
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        if f"Paper title: {trial['paper']['title']}\n" in previous["prompt"]:
            return
        path.rename(path.with_suffix(".previous.json"))
    abstract = trial["paper"]["abstract"]
    numbered = "\n".join(f"[{i}] {abstract[s:e]}" for i, (s, e) in enumerate(spans(abstract), 1))
    outcomes = [{"index": i, "title": o["measure_text"], "time_frame": o["time_frame"],
                 "description": o.get("description")} for i, o in enumerate(trial["outcomes"])]
    prompt = PROMPT.format(nct_id=trial["nct_id"], title=trial["paper"]["title"],
                           abstract=numbered, outcomes=json.dumps(outcomes, ensure_ascii=False))
    result = call_llm(prompt, Settings())
    data = None
    error = None
    if result.status == "ok":
        try:
            text = result.text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
            data = json.loads(text)
            labels = data["labels"]
            assert sorted(label["index"] for label in labels) == list(range(len(outcomes)))
            for label in labels:
                assert label["reported"] in ("yes", "no", "unclear")
                if label["reported"] == "yes":
                    assert label["role"] in ("primary", "secondary", "not_stated")
                    assert isinstance(label["sentence"], int) and 1 <= label["sentence"] <= len(spans(abstract))
        except (ValueError, TypeError, KeyError, AssertionError) as exc:
            error = type(exc).__name__
    write_json(path, {"nct_id": trial["nct_id"], "prompt": prompt,
                     "llm": result.model_dump(), "parsed": data, "validation_error": error})
    print(trial["nct_id"], result.status, error or "validated", flush=True)


def screen():
    (ROOT / "screening").mkdir(exist_ok=True)
    trials = json.loads((ROOT / "candidates.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(screen_trial, trials))


def load_pool():
    pool = []
    overrides_path = ROOT / "screening_corrections.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {}
    for trial in json.loads((ROOT / "candidates.json").read_text(encoding="utf-8")):
        path = ROOT / "screening" / f"{trial['nct_id']}.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if result["validation_error"] or result["llm"]["status"] != "ok":
            continue
        for label in result["parsed"]["labels"]:
            label = dict(label)
            if label["reported"] == "yes":
                # Remap the screen's sentence number to the improved sheet splitter.
                old_sentences = dict((int(n), text) for n, text in
                                     re.findall(r"(?m)^\[(\d+)\] ([^\n]*)", result["prompt"]))
                old_quote = old_sentences.get(label["sentence"])
                if not old_quote:
                    continue
                start = trial["paper"]["abstract"].find(old_quote)
                for number, (s, e) in enumerate(spans(trial["paper"]["abstract"]), 1):
                    if s <= start < e:
                        label["sentence"] = number
                        break
            correction = overrides.get(f"{trial['nct_id']}:{label['index']}")
            if correction:
                label = {**label, **correction}
            if label.get("uncertainty") == "high":
                continue
            pool.append((trial, label, result["llm"]["model"]))
    return pool


def pick(pool, quota):
    """Round-robin trials; at most five cases and three positives per trial."""
    selected = []
    counts = Counter()
    trial_counts = Counter()
    positive_counts = Counter()
    evidence_counts = Counter()
    remaining = list(pool)
    while remaining:
        available = []
        for index, (trial, label, model) in enumerate(remaining):
            nct = trial["nct_id"]
            category = label["reported"]
            evidence = (nct, label.get("sentence"))
            if counts[category] >= quota[category] or trial_counts[nct] >= 5:
                continue
            if category == "yes" and (positive_counts[nct] >= 3 or evidence_counts[evidence] >= 1):
                continue
            # Prefer role diversity when choosing among a trial's positives.
            secondary_priority = 0 if category == "yes" and label.get("role") == "secondary" else 1
            available.append((trial_counts[nct], secondary_priority, index))
        if not available:
            break
        _, _, index = min(available)
        item = remaining.pop(index)
        trial, label, model = item
        nct = trial["nct_id"]
        selected.append(item)
        counts[label["reported"]] += 1
        trial_counts[nct] += 1
        if label["reported"] == "yes":
            positive_counts[nct] += 1
            evidence_counts[(nct, label["sentence"])] += 1
        if dict(counts) == quota:
            break
    return selected, counts


def build():
    if (ROOT / "cases.jsonl").exists():
        raise SystemExit("Refusing to overwrite cases.jsonl")
    pool = load_pool()
    trials = sorted({item[0]["nct_id"] for item in pool})
    rng = random.Random(SEED)
    solution = None
    # Label-stratified trial split: selection uses provisional labels, never system outputs.
    for _ in range(2000):
        shuffled = rng.sample(trials, len(trials))
        test_ids = set(shuffled[:max(8, len(trials) // 5)])
        test, test_counts = pick([p for p in pool if p[0]["nct_id"] in test_ids], QUOTAS["held_out"])
        if dict(test_counts) != QUOTAS["held_out"]:
            continue
        if sum(p[1].get("role") == "secondary" for p in test if p[1]["reported"] == "yes") < 2:
            continue
        test_pmids = {p[0]["paper"]["pmid"] for p in test}
        dev, dev_counts = pick([p for p in pool if p[0]["nct_id"] not in test_ids
                               and p[0]["paper"]["pmid"] not in test_pmids], QUOTAS["dev"])
        if dict(dev_counts) == QUOTAS["dev"] and sum(p[1].get("role") == "secondary" for p in dev if p[1]["reported"] == "yes") >= 6:
            solution = {"dev": dev, "held_out": test}
            break
    if solution is None:
        print("Pool counts:", Counter(p[1]["reported"] for p in pool))
        raise SystemExit("Cannot meet balance/diversity constraints: screen additional trials")
    cases = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for split, items in solution.items():
        for trial, label, model in items:
            outcome = trial["outcomes"][label["index"]]
            paper = trial["paper"]
            abstract = paper["abstract"]
            quote = None
            if label["reported"] == "yes":
                start, end = spans(abstract)[label["sentence"] - 1]
                quote = {"text": abstract[start:end], "start": start, "end": end}
            cases.append({
                "case_id": f"b{len(cases)+1:03}", "split": split, "nct_id": trial["nct_id"],
                "registry_content_hash": trial["snapshot"]["content_hash"],
                "source_url": trial["snapshot"]["source_url"],
                "paper": {"pmid": paper["pmid"], "title": paper["title"], "raw_hash": paper["raw_hash"],
                          "abstract_sha256": hashlib.sha256(abstract.encode()).hexdigest(),
                          "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/"},
                "registered": {k: outcome.get(k) for k in ("role", "position", "measure_text", "time_frame", "description")},
                "abstract": abstract,
                "label": {"reported": label["reported"], "reported_role": label["role"] if quote else None,
                          "quote": quote, "notes": label["reason"], "annotator": f"LLM-suggested ({model}); not human verified",
                          "labelled_at": now, "review_status": "provisional", "uncertainty": label.get("uncertainty")},
            })
            cases[-1]["label"]["screening_review"] = label.get("reviewer")
    text = "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n"
    (ROOT / "cases.jsonl").write_text(text, encoding="utf-8")
    # Separate files allow development work without displaying held-out material.
    for split in solution:
        chosen = [c for c in cases if c["split"] == split]
        (ROOT / f"{split}.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in chosen)+"\n", encoding="utf-8")
        lines = [f"# V3 {split}: provisional labeling sheet", "", "Labels are suggestions for human adjudication, not gold truth.", ""]
        for c in chosen:
            lines.extend([f"## {c['case_id']} ({c['nct_id']}, PMID {c['paper']['pmid']})",
                          f"Outcome: {c['registered']['measure_text']}", f"Time frame: {c['registered']['time_frame']}",
                          f"Suggested label: **{c['label']['reported']}**, role: {c['label']['reported_role']}",
                          f"Reason: {c['label']['notes']}", ""])
            for i, (s,e) in enumerate(spans(c["abstract"]), 1):
                lines.append(f"[{i}] {c['abstract'][s:e]}")
            lines.append("")
        (ROOT / f"{split}_label_sheet.md").write_text("\n".join(lines), encoding="utf-8")
    check()


def check():
    cases = read_jsonl(ROOT / "cases.jsonl")
    assert len(cases) == 100
    assert len({c["case_id"] for c in cases}) == 100
    old_trials = set()
    for path in [ROOT.parent / "cases.jsonl", ROOT.parent / "v2/cases.jsonl"]:
        old_trials.update(c["nct_id"] for c in read_jsonl(path))
    for split, quota in QUOTAS.items():
        chosen = [c for c in cases if c["split"] == split]
        assert Counter(c["label"]["reported"] for c in chosen) == quota
        assert max(Counter(c["nct_id"] for c in chosen).values()) <= 5
        positives = [c for c in chosen if c["label"]["reported"] == "yes"]
        assert max(Counter(c["nct_id"] for c in positives).values()) <= 3
        assert {c["label"]["reported_role"] for c in positives} == {"primary", "secondary", "not_stated"}
        evidence_keys = [(c["nct_id"], c["label"]["quote"]["start"], c["label"]["quote"]["end"]) for c in positives]
        assert len(set(evidence_keys)) == len(evidence_keys)
        print(split, len(chosen), dict(Counter(c["label"]["reported"] for c in chosen)),
              'trials', len({c["nct_id"] for c in chosen}))
    for key in ("nct_id",):
        assert not ({c[key] for c in cases if c["split"] == "dev"} & {c[key] for c in cases if c["split"] == "held_out"})
    for key in ("pmid", "abstract_sha256"):
        assert not ({c["paper"][key] for c in cases if c["split"] == "dev"} & {c["paper"][key] for c in cases if c["split"] == "held_out"})
    assert not old_trials & {c["nct_id"] for c in cases}
    assert len({(c['nct_id'],c['registered']['role'],c['registered']['position'],c['paper']['pmid']) for c in cases}) == 100
    for c in cases:
        assert hashlib.sha256(c["abstract"].encode()).hexdigest() == c["paper"]["abstract_sha256"]
        q = c["label"]["quote"]
        if c["label"]["reported"] == "yes":
            assert q and c["abstract"][q["start"]:q["end"]] == q["text"]
        else:
            assert q is None and c["label"]["reported_role"] is None
    print('Verified quotas, uniqueness, new trials, split isolation, hashes and exact quotes.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "screen", "build", "check"])
    action = parser.parse_args().action
    ROOT.mkdir(exist_ok=True)
    globals()[action]()
