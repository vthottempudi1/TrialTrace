"""Independent reference format and deterministic one-to-one matching for v4."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trialtrace.safety.safety_baseline import safety_sentence_spans


class ReferenceStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentence: int = Field(ge=1)
    quote: str
    kind: Literal["death", "serious", "event"]
    term: str | None
    arm: str | None
    population: str | None
    count: int | None = Field(ge=0)
    denominator: int | None = Field(ge=1)
    percent: float | None = Field(ge=0, le=100)
    grade: str | None
    treatment_related: bool
    cause: str | None
    all_cause: bool
    measure: Literal["participant_proportion", "time_to_event"]
    event_scope: Literal["unspecified", "serious", "non_serious", "all"]
    detail_level: Literal["total", "component", "unspecified"]
    claim_family: str = Field(min_length=1)
    attribution: Literal["target", "other", "pooled", "unresolved"]
    category: Literal["safety_consistent", "safety_number_differs", "safety_not_comparable",
                      "safety_unclear", "safety_in_paper_not_registry", "unknown"] | None
    acceptable_arm_ids: list[str]
    acceptable_event_ids: list[str]
    window: Literal["same", "different", "unknown"]
    population_relation: Literal["same", "different", "unknown"]
    abstract_window_quote: str | None
    registry_window_quote: str | None
    abstract_population_quote: str | None = None
    registry_population_quote: str | None = None
    aliases: dict[str, list[str]] = Field(default_factory=dict)


class ReferenceCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    document: Literal["trial_results", "protocol", "review", "other"]
    statements: list[ReferenceStatement]
    notes: str = ""


FIELDS = ("arm", "population", "count", "denominator", "percent", "grade", "treatment_related",
          "cause", "all_cause", "measure", "event_scope", "detail_level")


def normalized(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else value


def field_correct(gold, predicted, field):
    expected = getattr(gold, field)
    alternatives = [expected] + gold.aliases.get(field, [])
    return normalized(getattr(predicted, field)) in [normalized(x) for x in alternatives]


def matches(gold, predicted):
    if gold.kind != predicted.kind or gold.sentence != predicted.sentence:
        return False
    if gold.kind == "event" and normalized(predicted.term) not in [
            normalized(gold.term)] + [normalized(t) for t in gold.aliases.get("term", [])]:
        return False
    return predicted.count == gold.count if gold.count is not None else predicted.percent == gold.percent


def pair_statements(labels, statements):
    """Maximum-cardinality one-to-one matching; field agreement breaks ties.

    The fixed reference order breaks remaining ties. Never match on category,
    approved candidate IDs, or human window labels.
    """
    edges = {i: sorted((j for j, st in enumerate(statements) if matches(gold, st)),
                        key=lambda j: (-sum(field_correct(gold, statements[j], f) for f in FIELDS), j))
             for i, gold in enumerate(labels)}
    assigned = {}

    def assign(i, visited):
        for j in edges[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in assigned or assign(assigned[j], visited):
                assigned[j] = i
                return True
        return False

    for i in range(len(labels)):
        assign(i, set())
    return {i: j for j, i in assigned.items()}


def validate_case(reference, case, safety):
    if reference.case_id != case["case_id"]:
        raise ValueError("Reference case ID differs from source")
    if reference.document != "trial_results" and reference.statements:
        raise ValueError("Non-results documents cannot have eligible reference statements")
    arms = {f"arm:{arm.group_id}" for arm in safety.arms}
    events = {f"event:{event.kind}:{event.position}" for event in safety.events}
    spans = safety_sentence_spans(case["abstract"])
    for st in reference.statements:
        if not set(st.aliases) <= {"arm", "population", "term", "grade", "cause"}:
            raise ValueError("Aliases cannot change numeric values or semantic flags")
        if st.sentence > len(spans):
            raise ValueError("Reference sentence is outside the abstract")
        start, end = spans[st.sentence - 1]
        if not st.quote.strip() or st.quote not in case["abstract"][start:end]:
            raise ValueError("Reference quote must be exact text in its numbered sentence")
        if st.count is None and st.percent is None:
            raise ValueError("Reference has no participant count/percentage")
        if st.kind == "event" and not st.term:
            raise ValueError("Named event needs a term")
        if not set(st.acceptable_arm_ids) <= arms or not set(st.acceptable_event_ids) <= events:
            raise ValueError("Reference uses an absent candidate ID")
        if st.kind != "event" and st.acceptable_event_ids:
            raise ValueError("Death/serious totals cannot match a named-event row")
        if st.attribution == "target" and st.category is None:
            raise ValueError("Target statement needs a comparison category")
        if st.attribution != "target" and st.category is not None:
            raise ValueError("Non-target statements cannot have target comparison categories")
        if st.window != "unknown":
            if (not st.abstract_window_quote or st.abstract_window_quote not in case["abstract"]
                    or not st.registry_window_quote or st.registry_window_quote not in (safety.time_frame or "")):
                raise ValueError("Known window requires exact quotes from both sources")
        if st.population_relation != "unknown":
            registry_population = "\n".join(arm.title + "\n" + (arm.description or "") for arm in safety.arms
                                             if f"arm:{arm.group_id}" in st.acceptable_arm_ids)
            if (not st.abstract_population_quote or st.abstract_population_quote not in case["abstract"]
                    or not st.registry_population_quote or st.registry_population_quote not in registry_population):
                raise ValueError("Known population relation requires exact quotes for the identified arm")
        if st.category in ("safety_consistent", "safety_number_differs"):
            if (st.window != "same" or st.population_relation != "same" or st.denominator is None
                    or st.measure != "participant_proportion" or len(st.acceptable_arm_ids) != 1):
                raise ValueError("Numeric verdict requires verified scope, denominator and a single arm")
