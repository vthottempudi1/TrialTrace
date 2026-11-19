##############################################################################
# trialtrace/safety/safety_compare.py
#
# PURPOSE: Safety, step S7: compare ONE safety statement read from an abstract (by the model reader,
#          already checked by code) with the registry's adverse-event table, and give it a category.
#          Plain code, no LLM. Nothing is stored here (the roll-up and findings are step S8).
#
# STEPS, in this order (comparability first, numbers last):
#   1. Is there registry data, and did the statement pass its code checks?   no -> unknown
#   2. Same definition?  a grade, "treatment-related" or one cause of death  -> safety_not_comparable
#   3. Same group?       the abstract's group is matched to ONE registry group by its title
#                        no certain match                                    -> safety_unclear
#   4. The registry's number for that group (deaths, serious, or the named event's row)
#                        registry gives no death / serious count             -> safety_in_paper_not_registry
#                        no event row with that name                         -> safety_unclear
#                        the event is in several rows (never added up)       -> safety_not_comparable
#   5. Same population?  the abstract must state its denominator and it must
#                        equal the registry's number at risk; otherwise      -> safety_not_comparable
#   6. The numbers:      agree -> safety_consistent      differ -> safety_number_differs
#   Then, per trial: deaths or serious events the registry gives but no statement mentions
#                                                                            -> safety_registry_not_in_abstract
#
# LIMITS:  the time window is NOT checked (an abstract statement carries none): said in every answer.
#          Names are matched by words, not meaning: a group or event written differently is "unclear".
#          A difference is for review. It never says anyone did something wrong.
#
# USE:     comparisons = compare_statements(reading.statements, safety)     # list of SafetyComparison
##############################################################################

import re

from pydantic import BaseModel, Field

from trialtrace.compare.abstract_baseline import TextQuote
from trialtrace.safety.safety_baseline import EVENT_TERMS
from trialtrace.safety.safety_llm import ModelSafetyStatement
from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyExtraction
from trialtrace.safety.safety_parser import safety_path

PERCENT_TOLERANCE = 1.0     # a written percent may differ from the registry's by rounding (percentage points); counts must be equal
WINDOW_NOTE = "The time window is unverified unless a source-bound human review explicitly confirms comparability."
ARM_FILLER = {"the", "group", "groups", "arm", "arms", "cohort", "patients", "participants", "recipients"}   # words that do not name a group


class SafetyComparison(BaseModel):
    """One abstract statement set beside the registry's number, with the category and the reason in plain words."""

    category: str                           # safety_consistent, safety_number_differs, safety_not_comparable, ...
    reason: str
    reason_code: str | None = None           # machine-readable first blocking reason
    kind: str | None = None                 # death, serious, event
    term: str | None = None
    paper_arm: str | None = None            # the group as the abstract writes it
    paper_count: int | None = None
    paper_denominator: int | None = None
    paper_percent: float | None = None
    registry_arm: str | None = None         # the matched registry group's title
    registry_affected: int | None = None
    registry_at_risk: int | None = None
    registry_values: list[tuple[str, str]] = Field(default_factory=list)   # (path in the record, value) of each registry value used: the evidence
    sentence: int | None = None
    quote: TextQuote | None = None
    note: str = WINDOW_NOTE


def name_words(text: str) -> list[str]:
    """The words of a name in small letters, without punctuation: "T-DXd 5.4 mg/kg" -> t, dxd, 5, 4, mg, kg."""
    return re.findall(r"[a-z0-9]+", text.lower())


def match_arm(paper_arm: str | None, arms: list[SafetyArm]) -> SafetyArm | None:
    """The ONE registry group the abstract's group names, or None when that is not certain."""
    if paper_arm is None:
        if len(arms) == 1:
            return arms[0]                  # no group stated and the registry has only one
        return None

    paper_words = set(name_words(paper_arm)) - ARM_FILLER
    if not paper_words:
        return None

    same = []                               # titles with exactly the same words
    close = []                              # one name's words are all inside the other
    for arm in arms:
        title_words = set(name_words(arm.title)) - ARM_FILLER
        if not title_words:
            continue
        if title_words == paper_words:
            same.append(arm)
        elif paper_words <= title_words or title_words <= paper_words:
            close.append(arm)
    if len(same) == 1:
        return same[0]
    if len(same) == 0 and len(close) == 1:
        return close[0]
    return None                             # none, or more than one: not certain


def event_spellings(term: str) -> list[str]:
    """Every spelling we treat as the same event: "anemia" -> anaemia, anemia. An unlisted event -> itself only."""
    name = " ".join(name_words(term))
    for spellings in EVENT_TERMS.values():
        names = []
        for spelling in spellings:
            names.append(" ".join(name_words(spelling)))
        if name in names:
            return names
    return [name]


def find_event_rows(term: str, events: list[SafetyEvent]) -> list[SafetyEvent]:
    """The registry rows (serious and non-serious) whose term is this event."""
    names = event_spellings(term)
    rows = []
    for event in events:
        if event.term is not None and " ".join(name_words(event.term)) in names:
            rows.append(event)
    return rows


def numbers_text(affected: int | None, at_risk: int | None, percent: float | None = None) -> str:
    """'42 of 101 (41.6%)', with only the parts that are given."""
    parts = []
    if affected is not None and at_risk is not None:
        parts.append(f"{affected} of {at_risk}")
    elif affected is not None:
        parts.append(str(affected))
    if percent is not None:
        parts.append(f"{percent:g}%")
    return " / ".join(parts) if parts else "no number"


def compare_statement(statement: ModelSafetyStatement, safety: SafetyExtraction | None, *, population_verified=False) -> SafetyComparison:
    """Check inputs, definition, group and population before comparing numbers.

    Each step fills in the result and stops early if comparison is uncertain.
    Only the last step can report that the numbers agree or differ.
    """
    result = SafetyComparison(
        category="unknown", reason="", kind=statement.kind, term=statement.term, paper_arm=statement.arm,
        paper_count=statement.count, paper_denominator=statement.denominator, paper_percent=statement.percent,
        sentence=statement.sentence, quote=statement.quote,
    )
    # Stop as soon as a step explains why the numbers cannot be compared.
    if not check_comparison_inputs(statement, safety, result):
        return result
    if not check_same_definition(statement, result):
        return result
    arm = find_comparison_arm(statement, safety, result)
    if arm is None:
        return result
    if (statement.population_scope_unverified or statement.population is not None) and not population_verified:
        result.category = "safety_not_comparable"
        result.reason_code = "population_subset_unverified"
        result.reason = "The abstract reports a restricted subgroup. Matching its treatment arm does not establish the same population as the registry. Review the population separately."
        return result
    if not read_registry_numbers(statement, safety, arm, result):
        return result
    if not check_same_population(statement, result):
        return result
    compare_checked_numbers(statement, result)
    return result


def check_comparison_inputs(statement: ModelSafetyStatement, safety: SafetyExtraction | None, result: SafetyComparison) -> bool:
    """Return False with a reason if either input is unavailable or unchecked."""
    if safety is None or safety.status != "ok" or not safety.arms:
        result.reason_code = "registry_data_missing"
        result.reason = "The registry record gives no adverse-event data for this version."
        return False
    if statement.error_code is not None or statement.kind is None:
        result.reason_code = "statement_not_validated"
        result.reason = "The statement did not pass the code checks, so it is not used."
        return False
    return True


def check_same_definition(statement: ModelSafetyStatement, result: SafetyComparison) -> bool:
    """Return False if the abstract counts a narrower definition than the registry."""
    result.category = "safety_not_comparable"
    if statement.measure == "time_to_event":
        result.reason_code = "time_to_event_estimate"
        result.reason = "The abstract reports a time-to-event estimate; the registry table gives raw participant counts. Review the measure and time point."
        return False
    if statement.grade is not None and statement.kind != "death":
        result.reason_code = "grade_restricted"
        result.reason = f'The abstract gives a number for "{statement.grade}"; the registry table has no grades.'
        return False
    if statement.treatment_related:
        result.reason_code = "treatment_related_only"
        result.reason = "The abstract counts treatment-related cases only; the registry counts all cases."
        return False
    if statement.cause is not None:
        result.reason_code = "specific_death_cause"
        result.reason = f'The abstract counts deaths from one cause ("{statement.cause}"); the registry counts deaths from all causes.'
        return False
    return True


def find_comparison_arm(statement: ModelSafetyStatement, safety: SafetyExtraction, result: SafetyComparison) -> SafetyArm | None:
    """Find one certain registry group; otherwise record why the group is unclear."""
    result.category = "safety_unclear"
    if statement.arm is not None and statement.arm_in_quote is False:
        result.reason_code = "group_outside_quote"
        result.reason = f'The group "{statement.arm}" is named outside this event clause, so code cannot tie it to this number without review.'
        return None
    arm = match_arm(statement.arm, safety.arms)
    if arm is None:
        if statement.arm is None:
            result.reason_code = "group_not_stated"
            result.reason = f"The abstract states no group and the registry lists {len(safety.arms)} groups (groups are never added up)."
        else:
            result.reason_code = "group_name_unresolved"
            result.reason = f'The group "{statement.arm}" could not be matched to exactly one registry group by its title.'
        return None
    result.registry_arm = arm.title
    return arm


def read_registry_numbers(statement: ModelSafetyStatement, safety: SafetyExtraction, arm: SafetyArm, result: SafetyComparison) -> bool:
    """Find the registry count for this group, without adding event rows together."""
    paper = numbers_text(statement.count, statement.denominator, statement.percent)
    if statement.kind == "death":
        affected, at_risk = arm.deaths_affected, arm.deaths_at_risk
        affected_path = safety_path("eventGroups", arm.position, "deathsNumAffected")
        at_risk_path = safety_path("eventGroups", arm.position, "deathsNumAtRisk")
    elif statement.kind == "serious":
        affected, at_risk = arm.serious_affected, arm.serious_at_risk
        affected_path = safety_path("eventGroups", arm.position, "seriousNumAffected")
        at_risk_path = safety_path("eventGroups", arm.position, "seriousNumAtRisk")
    else:
        rows = find_event_rows(statement.term or "", safety.events)
        if len(rows) == 0:
            result.reason_code = "event_name_unresolved"
            result.reason = f'No registry event is named "{statement.term}". The registry may use another word for it.'
            return False
        if statement.event_scope in ("serious", "non_serious"):
            wanted = "serious" if statement.event_scope == "serious" else "other"
            rows = [row for row in rows if row.kind == wanted]
            if not rows:
                result.category = "safety_not_comparable"
                result.reason_code = "event_seriousness_mismatch"
                result.reason = "The abstract and registry event rows describe different seriousness scopes."
                return False
        if len(rows) > 1:
            result.category = "safety_not_comparable"
            result.reason_code = "event_multiple_rows"
            result.reason = (f'The registry lists "{statement.term}" in {len(rows)} rows (serious and non-serious, or twice). '
                             "One participant can be in several rows, so rows are never added up.")
            return False
        row = rows[0]
        if row.kind == "serious" and statement.event_scope != "serious" and safety.frequency_threshold != 0:
            result.category = "safety_not_comparable"
            result.reason_code = "event_serious_only"
            result.reason = (f'The registry lists "{statement.term}" only as a serious event; non-serious cases below '
                             "its listing threshold are not shown, so the registry number can be lower.")
            return False
        affected, at_risk = None, None
        list_name = "seriousEvents" if row.kind == "serious" else "otherEvents"
        for index, count in enumerate(row.counts):
            if count.group_id == arm.group_id:
                affected, at_risk = count.affected, count.at_risk
                affected_path = safety_path(list_name, row.position, "stats", index, "numAffected")
                at_risk_path = safety_path(list_name, row.position, "stats", index, "numAtRisk")
                break
        result.registry_values.append((safety_path(list_name, row.position, "term"), row.term))
    result.registry_affected = affected
    result.registry_at_risk = at_risk
    result.registry_values.append((safety_path("eventGroups", arm.position, "title"), arm.title))
    if affected is not None:
        result.registry_values.append((affected_path, str(affected)))
    if at_risk is not None:
        result.registry_values.append((at_risk_path, str(at_risk)))

    if affected is None:
        if statement.kind == "event":
            result.category = "unknown"
            result.reason_code = "event_group_count_missing"
            result.reason = f'The registry row for "{statement.term}" gives no number for the group "{arm.title}".'
        else:
            result.category = "safety_in_paper_not_registry"
            what = "deaths" if statement.kind == "death" else "participants with a serious adverse event"
            result.reason_code = "registry_count_missing"
            result.reason = (f'The abstract reports {paper} for {what}; the registry gives no such count for the group '
                             f'"{arm.title}" (not given is unknown, not zero).')
        return False
    return True


def check_same_population(statement: ModelSafetyStatement, result: SafetyComparison) -> bool:
    """Return False unless both sources explicitly use the same population size."""
    paper = numbers_text(statement.count, statement.denominator, statement.percent)
    at_risk = result.registry_at_risk
    registry = numbers_text(result.registry_affected, at_risk)
    result.category = "safety_not_comparable"
    if statement.denominator is None:
        result.reason_code = "paper_denominator_missing"
        result.reason = (f"The abstract ({paper}) does not state how many participants the number is out of, so the "
                         f"population cannot be checked against the registry ({registry}).")
        return False
    if not at_risk:
        result.reason_code = "registry_population_missing"
        result.reason = f"The registry does not give the number of participants at risk ({registry}); the abstract reports {paper}."
        return False
    if statement.denominator != at_risk:
        result.reason_code = "population_size_differs"
        result.reason = (f"Different populations: the abstract reports {paper}, the registry {registry}. "
                         "The numbers are not compared.")
        return False
    return True


def compare_checked_numbers(statement: ModelSafetyStatement, result: SafetyComparison) -> None:
    """Compare counts or percentages after all comparability checks have passed."""
    paper = numbers_text(statement.count, statement.denominator, statement.percent)
    affected = result.registry_affected
    at_risk = result.registry_at_risk
    registry = numbers_text(affected, at_risk)
    if statement.count is not None:
        agree = statement.count == affected
    else:
        agree = abs(statement.percent - 100 * affected / at_risk) <= PERCENT_TOLERANCE
    if agree:
        result.category = "safety_consistent"
        result.reason_code = "numbers_agree"
        result.reason = f"The abstract reports {paper}; the registry lists {registry} for the same group."
    else:
        result.category = "safety_number_differs"
        result.reason_code = "numbers_differ"
        result.reason = f"The abstract reports {paper}; the registry lists {registry} for the same group. For review."


def registry_not_in_abstract(statements: list[ModelSafetyStatement], safety: SafetyExtraction | None) -> list[SafetyComparison]:
    """Deaths and serious events the registry gives but no statement mentions. Expected: abstracts are short.

    Named events are not listed here: a registry table can hold hundreds, and an abstract names a few.
    """
    if safety is None or safety.status != "ok":
        return []
    found = []
    for kind, what in (("death", "deaths"), ("serious", "participants with a serious adverse event")):
        mentioned = False
        for statement in statements:
            if statement.kind == kind:
                mentioned = True
        given = False
        for arm in safety.arms:
            number = arm.deaths_affected if kind == "death" else arm.serious_affected
            if number is not None:
                given = True
        if given and not mentioned:
            found.append(SafetyComparison(
                category="safety_registry_not_in_abstract", kind=kind,
                reason=f"The registry gives {what} per group; the abstract states no number for them. "
                       "Not in the abstract does not mean not in the paper.",
            ))
    return found


def compare_statements(statements: list[ModelSafetyStatement], safety: SafetyExtraction | None) -> list[SafetyComparison]:
    """Every statement of one abstract compared, then what the registry gives and the abstract leaves out."""
    comparisons = []
    for statement in statements:
        comparisons.append(compare_statement(statement, safety))
    return comparisons + registry_not_in_abstract(statements, safety)
