##############################################################################
# trialtrace/safety/safety_rollup.py
#
# PURPOSE: Safety, step S8: ONE finding per safety quantity of a trial (deaths, serious adverse events,
#          each named event), across every paper abstract read. The report and the review queue show these.
#
# STEPS:
#   1. Take the newest stored safety readings of the trial (written by read-safety).
#   2. Compare every model statement with the registry table (compare/safety_compare.py, step S7).
#   3. Group the comparisons by quantity: "death", "serious", or the event's name.
#   4. Roll up: the category that most needs a person wins
#        number differs > in paper not registry > unclear > not comparable > consistent
#   5. A quantity only the code baseline found (the model listed nothing)  -> safety_readers_differ
#      (not when the model lists an event from the same sentence: that is only another name)
#      Deaths / serious events the registry gives and no reader found       -> safety_registry_not_in_abstract
#      ... but if a listed paper was not read or its reading failed          -> unknown
#
# RULES:   every finding is provisional until a person reviews it; "not in the abstract" never means
#          "not in the paper"; a difference is for review and judges nobody.
#
# USE:     findings = check_safety_summary(conn, "NCT04644237")        # in the report
#          evidence = registry_evidence(snapshot, data)                 # saved by read-safety
##############################################################################

import psycopg
from typing import Callable
from pydantic import BaseModel, Field

from trialtrace.compare.abstract_baseline import TextQuote
from trialtrace.compare.paper_outcomes import PROVISIONAL, abstract_evidence_id
from trialtrace.safety.safety_baseline import EVENT_TERMS
from trialtrace.safety.safety_compare import WINDOW_NOTE, SafetyComparison, compare_statement, name_words
from trialtrace.safety.safety_llm import ModelSafetyStatement
from trialtrace.contracts import Evidence, Finding, SafetyExtraction, Snapshot
from trialtrace.db.repository import get_latest_snapshot, get_papers_for_trial, get_safety, get_safety_readings

# the category that most needs a person comes first
ATTENTION_ORDER = ["safety_number_differs", "safety_in_paper_not_registry", "safety_unclear", "safety_not_comparable", "safety_consistent"]

# review priority of the safety categories (used by the review queue)
SAFETY_HIGH_RISK = {"safety_number_differs", "safety_in_paper_not_registry", "safety_unclear", "safety_not_comparable", "safety_readers_differ"}
SAFETY_LOW_RISK = {"safety_consistent", "safety_registry_not_in_abstract"}

SAFETY_ABSTRACT_ONLY = "Abstract only: a number that is not in the abstract may still be in the full paper."

LEAD_SENTENCE = {
    "safety_consistent": "The abstract number(s) for {what} agree with the registry's.",
    "safety_number_differs": "An abstract number for {what} differs from the registry's, for review.",
    "safety_in_paper_not_registry": "An abstract gives a number for {what} that the registry record does not give.",
    "safety_unclear": "An abstract number for {what} could not be matched to one registry group or event with certainty.",
    "safety_not_comparable": "The abstract number(s) for {what} cannot be compared with the registry's (different definition or population).",
}


class ComparedStatement(BaseModel):
    """One model statement of one paper, after the S7 comparison."""

    pmid: str
    raw_hash: str
    name: str                               # the quantity: death, serious, or the event's name
    comparison: SafetyComparison


class BaselineMention(BaseModel):
    """One baseline statement that has a number (or says "none"): only that a quantity was found, and where."""

    pmid: str
    raw_hash: str
    name: str
    quote_start: int
    quote_end: int


class TrialSafety(BaseModel):
    """Everything the roll-up needs for one trial, from its newest safety reading run."""

    nct_id: str
    run_id: str
    registry_hash: str
    model: str | None = None
    prompt_hash: str | None = None
    compared: list[ComparedStatement] = Field(default_factory=list)
    baseline_used: bool = False
    baseline: list[BaselineMention] = Field(default_factory=list)
    read_papers: dict[str, str] = Field(default_factory=dict)       # PMID -> raw hash, for papers the model read
    failed_pmids: list[str] = Field(default_factory=list)           # the model's answer could not be used, or no abstract
    unread_pmids: list[str] = Field(default_factory=list)           # listed papers with an abstract that the run did not read
    overlapping_evidence: bool = False
    validation_notes: list[str] = Field(default_factory=list)
    registry_gives: list[str] = Field(default_factory=list)         # "death" / "serious" if any group has that count


def quantity_name(kind: str, term: str | None) -> str:
    """death, serious, or one name per event: "Anemia" and "anaemia" are both "anaemia"."""
    if kind != "event":
        return kind
    name = " ".join(name_words(term or ""))
    for key, spellings in EVENT_TERMS.items():
        for spelling in [key] + spellings:
            if " ".join(name_words(spelling)) == name:
                return key
    return name


def what_text(name: str) -> str:
    if name == "death":
        return "deaths"
    if name == "serious":
        return "participants with a serious adverse event"
    return f'the event "{name}"'


def rollup_category(categories: list[str]) -> str:
    """One category from the comparisons of one quantity: the one that most needs a person."""
    for category in ATTENTION_ORDER:
        if category in categories:
            return category
    return "unknown"


def statement_from_row(row: dict) -> ModelSafetyStatement:
    """A stored safety_statements row (model, passed the checks) back into a statement."""
    quote = None
    if row["quote_text"] is not None:
        quote = TextQuote(text=row["quote_text"], start=row["quote_start"], end=row["quote_end"])
    return ModelSafetyStatement(
        kind=row["kind"], term=row["term"], arm=row["arm"], arm_in_quote=row["arm_in_quote"],
        count=row["affected"], denominator=row["denominator"], percent=row["percent"], grade=row["grade"],
        treatment_related=row["treatment_related"], cause=row["cause"], all_cause=row["all_cause"],
        negated=row["negated"], sentence=row["sentence"], quote=quote,
        measure=row.get("measure", "participant_proportion"), attribution_quote=row.get("attribution_quote"),
        population=row.get("population"), population_scope_unverified=row.get("population_scope_unverified", False),
        event_scope=row.get("event_scope", "unspecified"), evidence_group=row.get("evidence_group"),
        detail_level=row.get("detail_level", "unspecified"), validation_notes=row.get("validation_notes") or [],
    )


def build_trial_safety(
    nct_id: str, registry_hash: str, safety: SafetyExtraction, readings: list[dict], listed_pmids: list[str],
    comparison_fn: Callable | None = None,
) -> TrialSafety:
    """Compare every stored model statement with the registry and collect what the baseline found.

    readings are the rows of get_safety_readings (one run); listed_pmids are the trial's listed papers with an abstract.
    """
    data = TrialSafety(nct_id=nct_id, run_id=readings[0]["safety_run_id"], registry_hash=registry_hash)
    for arm in safety.arms:
        if arm.deaths_affected is not None and "death" not in data.registry_gives:
            data.registry_gives.append("death")
        if arm.serious_affected is not None and "serious" not in data.registry_gives:
            data.registry_gives.append("serious")

    seen_pmids = []
    for reading in readings:
        pmid = reading["pmid"]
        if pmid not in seen_pmids:
            seen_pmids.append(pmid)

        if reading["system"] == "baseline":
            data.baseline_used = True
            collect_baseline_mentions(data, reading)
        else:
            collect_model_comparisons(data, reading, safety, comparison_fn)

    for pmid in listed_pmids:
        if pmid not in seen_pmids:
            data.unread_pmids.append(pmid)
    return data


def collect_baseline_mentions(data: TrialSafety, reading: dict) -> None:
    """Keep baseline mentions only when they contain numbers or explicitly say none."""
    pmid, raw_hash = reading["pmid"], reading["paper_raw_hash"]
    for row in reading["statements"]:
        if row["numbers"] or row["negated"]:
            data.baseline.append(BaselineMention(
                pmid=pmid, raw_hash=raw_hash, name=quantity_name(row["kind"], row["term"]),
                quote_start=row["quote_start"], quote_end=row["quote_end"],
            ))


def collect_model_comparisons(data: TrialSafety, reading: dict, safety: SafetyExtraction, comparison_fn=None) -> None:
    """Record failed readings, or compare each accepted model statement with the registry."""
    pmid, raw_hash = reading["pmid"], reading["paper_raw_hash"]
    data.model = reading["model"]
    data.prompt_hash = reading["prompt_hash"]
    if reading["status"] != "ok":
        data.failed_pmids.append(pmid)
        return
    data.read_papers[pmid] = raw_hash
    evidence_groups = [row.get("evidence_group") for row in reading["statements"] if row.get("evidence_group")]
    if len(evidence_groups) != len(set(evidence_groups)):
        data.overlapping_evidence = True
    for row in reading["statements"]:
        data.validation_notes.extend(f"PMID {pmid}: {note}" for note in row.get("validation_notes") or [])
        if row["rejected_code"] is not None:
            if pmid not in data.failed_pmids:
                data.failed_pmids.append(pmid)
            continue                            # failed a code check: kept in the table, never used
        statement = statement_from_row(row)
        data.compared.append(ComparedStatement(
            pmid=pmid, raw_hash=raw_hash, name=quantity_name(statement.kind, statement.term),
            comparison=comparison_fn(statement, safety, reading) if comparison_fn else compare_statement(statement, safety),
        ))


def registry_evidence(snapshot: Snapshot, data: TrialSafety) -> list[Evidence]:
    """One Evidence per registry value a comparison used (group title, counts, event term). Saved by read-safety."""
    items = []
    seen = []
    for compared in data.compared:
        for path, value in compared.comparison.registry_values:
            if path in seen:
                continue
            seen.append(path)
            items.append(Evidence(
                evidence_id=f"{data.nct_id}:{data.registry_hash[:12]}:{path}",
                nct_id=data.nct_id, content_hash=data.registry_hash, source="clinicaltrials",
                source_url=f"https://clinicaltrials.gov/study/{data.nct_id}",
                field_path=path, value=value, retrieved_at=snapshot.retrieved_at,
            ))
    return items


def common_limitations(data: TrialSafety) -> list[str]:
    """What every safety summary says about its own limits."""
    limitations = [SAFETY_ABSTRACT_ONLY, PROVISIONAL, WINDOW_NOTE]
    limitations.extend(dict.fromkeys(data.validation_notes))
    if data.overlapping_evidence:
        limitations.append("Several statements share source evidence and may describe overlapping totals or components. They are not independent observations and must not be added together.")
    if data.failed_pmids:
        limitations.append(f"Some or all model statements could not be used for paper(s) {', '.join(data.failed_pmids)}; check the saved rejection or failure reasons.")
    if data.unread_pmids:
        limitations.append(f"Listed paper(s) not read in this run (PMID {', '.join(data.unread_pmids)}): run read-safety again.")
    if data.model:
        limitations.append(f"Read by {data.model} (prompt {data.prompt_hash}); code checked every quote and number, and did the comparison.")
    return limitations


def model_read_an_event_there(data: TrialSafety, mention: BaselineMention) -> bool:
    """True if the model lists a named event from the same sentence of the same paper.

    The two readers then only name the event differently ("infection/fever" against "infection"): not a difference.
    """
    for compared in data.compared:
        quote = compared.comparison.quote
        if compared.pmid != mention.pmid or compared.comparison.kind != "event" or quote is None:
            continue
        if quote.start < mention.quote_end and mention.quote_start < quote.end:
            return True
    return False


def quantity_finding(data: TrialSafety, name: str) -> Finding | None:
    """Choose the finding for deaths, serious events, or one named event.

    Use model comparisons first, then baseline-only mentions. If neither reader
    found it, check whether registry data and reading coverage justify an omission.
    """
    items = [compared for compared in data.compared if compared.name == name]
    mentions = []
    for mention in data.baseline:
        if mention.name != name:
            continue
        if not items and name not in ("death", "serious") and model_read_an_event_there(data, mention):
            continue                                # same sentence, another name for the event
        mentions.append(mention)
    incomplete = bool(data.failed_pmids or data.unread_pmids)
    base = {
        "nct_id": data.nct_id, "check": "safety_summary:" + name.replace(" ", "_"), "comparison_arm": "abstract",
        "match_method": "deterministic", "source_run_id": data.run_id,
        "evidence_completeness": "incomplete" if incomplete else "complete",
    }
    limitations = common_limitations(data)
    if any("Match reviewed by " in item.comparison.note for item in items):
        base["match_method"] = "semantic"

    # Choose one case: model comparisons, baseline-only mentions, or no reader mention.
    if items:
        return finding_from_comparisons(data, name, items, mentions, base, limitations)
    if mentions:
        return finding_from_baseline(data, name, mentions, base, limitations)
    return finding_without_mentions(data, name, base, limitations)


def finding_from_comparisons(
    data: TrialSafety, name: str, items: list[ComparedStatement], mentions: list[BaselineMention],
    base: dict, limitations: list[str],
) -> Finding:
    """Combine the model comparisons, keeping the category that needs most attention."""
    what = what_text(name)
    category = rollup_category([compared.comparison.category for compared in items])
    evidence = []
    for compared in items:
        comparison = compared.comparison
        group = f', group "{comparison.paper_arm}"' if comparison.paper_arm else ""
        limitations.append(f"PMID {compared.pmid}, sentence {comparison.sentence}{group}: [{comparison.reason_code or 'unspecified'}] {comparison.reason}")
        limitations.append(comparison.note)
        if comparison.quote is not None:
            evidence.append(abstract_evidence_id(data.nct_id, compared.raw_hash, compared.pmid, comparison.quote.start, comparison.quote.end))
        for path, value in comparison.registry_values:
            evidence.append(f"{data.nct_id}:{data.registry_hash[:12]}:{path}")
    if data.baseline_used and not mentions:
        limitations.append("The code baseline found no number for this in the abstracts; only the model did.")
    if category == "unknown":
        return Finding(**base, category="unknown", limitations=limitations,
                       claim=f"Whether the abstract number(s) for {what} agree with the registry is unknown.")
    pmids = sorted({compared.pmid for compared in items}, key=int)
    claim = (LEAD_SENTENCE[category].format(what=what)
             + f" ({len(items)} statement(s) in paper(s) {', '.join(pmids)}; details below.)")
    return Finding(**base, category=category, claim=claim, evidence_ids=list(dict.fromkeys(evidence)), limitations=limitations)


def finding_from_baseline(
    data: TrialSafety, name: str, mentions: list[BaselineMention], base: dict, limitations: list[str],
) -> Finding:
    """Flag a quantity found by the code reader but not by the model reader."""
    what = what_text(name)
    evidence = [abstract_evidence_id(data.nct_id, mention.raw_hash, mention.pmid, mention.quote_start, mention.quote_end) for mention in mentions]
    pmids = sorted({mention.pmid for mention in mentions}, key=int)
    limitations.append("The baseline cannot tell a safety number from an efficacy result (for example mortality as the outcome).")
    return Finding(
        **base, category="safety_readers_differ", evidence_ids=list(dict.fromkeys(evidence)), limitations=limitations,
        claim=f"The code baseline found a number for {what} in the abstract of paper(s) {', '.join(pmids)}; the model listed none.",
    )


def finding_without_mentions(data: TrialSafety, name: str, base: dict, limitations: list[str]) -> Finding | None:
    """Report an omission only when all papers were read; otherwise say unknown.

    Only deaths and serious events are reported here, not every registry event.
    """
    what = what_text(name)
    incomplete = bool(data.failed_pmids or data.unread_pmids)
    if name not in ("death", "serious") or name not in data.registry_gives:
        return None
    if incomplete or not data.read_papers:
        return Finding(**base, category="unknown", limitations=limitations,
                       claim=f"Whether the paper abstracts give a number for {what} is unknown: not every listed paper has a complete usable safety reading.")
    evidence = [abstract_evidence_id(data.nct_id, raw_hash, pmid) for pmid, raw_hash in data.read_papers.items()]
    return Finding(
        **base, category="safety_registry_not_in_abstract", evidence_ids=evidence, limitations=limitations,
        claim=(f"The registry gives {what} per group; no number for them was found in the abstract(s) of the "
               f"{len(data.read_papers)} paper(s) read ({', '.join(sorted(data.read_papers, key=int))})."),
    )


def summary_findings(data: TrialSafety) -> list[Finding]:
    """One finding per quantity: deaths, serious events, then each named event. Highest review priority first."""
    names = ["death", "serious"]
    for compared in data.compared:
        if compared.name not in names:
            names.append(compared.name)
    for mention in data.baseline:
        if mention.name not in names:
            names.append(mention.name)

    high, other, low = [], [], []
    for name in names:
        finding = quantity_finding(data, name)
        if finding is None:
            continue
        if finding.category in SAFETY_HIGH_RISK:
            high.append(finding)
        elif finding.category in SAFETY_LOW_RISK:
            low.append(finding)
        else:
            other.append(finding)
    return high + other + low


def load_trial_safety(conn: psycopg.Connection, nct_id: str) -> TrialSafety | None:
    """The trial's newest safety readings compared with its newest registry version. None = nothing to compare."""
    snapshot = get_latest_snapshot(conn, nct_id)
    if snapshot is None:
        return None
    safety = get_safety(conn, nct_id, snapshot.content_hash)
    if safety is None or safety.status != "ok" or not safety.arms:
        return None
    readings = get_safety_readings(conn, nct_id)
    if not readings:
        return None
    listed_pmids = []
    papers = get_papers_for_trial(conn, nct_id)
    for paper in papers:
        if paper["status"] == "listed" and paper["abstract"] and paper["pmid"] not in listed_pmids:
            listed_pmids.append(paper["pmid"])
    from trialtrace.config import Settings
    from trialtrace.safety.safety_assist import source_context, load_report_comparison, comparison_for_report
    directory = Settings().safety_match_dir
    by_source = {(paper["pmid"], paper["raw_hash"]): paper for paper in papers}

    def compare_for_report(statement, registry, reading):
        paper = by_source.get((reading["pmid"], reading["paper_raw_hash"]))
        if paper is None or not paper["abstract"]:
            result = comparison_for_report(statement, registry)
            result.category, result.reason_code = "unknown", "paper_version_unavailable"
            result.reason = "The abstract version used by this reading is unavailable; read safety again."
            return result
        context = source_context(nct_id, reading["pmid"], reading["paper_raw_hash"], paper["abstract"], statement, registry)
        return load_report_comparison(directory, context)

    return build_trial_safety(nct_id, snapshot.content_hash, safety, readings, sorted(listed_pmids, key=int),
                              comparison_fn=compare_for_report)


def check_safety_summary(conn: psycopg.Connection, nct_id: str) -> list[Finding]:
    """The safety comparison findings of one trial, for the report.

    A trial with no registry safety data gives none (the registry finding already says so). A trial with
    registry data that was never read gives one 'unknown': not checked is never "nothing differs".
    """
    snapshot = get_latest_snapshot(conn, nct_id)
    if snapshot is None:
        return []
    safety = get_safety(conn, nct_id, snapshot.content_hash)
    if safety is None or safety.status != "ok" or not safety.arms:
        return []
    data = load_trial_safety(conn, nct_id)
    if data is None:
        return [Finding(
            nct_id=nct_id, check="safety_summary", category="unknown", comparison_arm="abstract", match_method="deterministic",
            claim="Whether the paper abstracts' safety numbers agree with the registry has not been checked.",
            limitations=["No safety reading is stored for this trial. Run: python -m trialtrace read-safety.", SAFETY_ABSTRACT_ONLY],
        )]
    return summary_findings(data)
