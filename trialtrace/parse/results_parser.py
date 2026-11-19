##############################################################################
# trialtrace/parse/results_parser.py
#
# PURPOSE: Read the outcomes a trial REPORTED in its posted results (the
#          registry's resultsSection) from one saved registry file.
#          This is the "structured results" arm: two structured records to compare,
#          so no LLM and no PDF reading. Not every trial has one (only those that
#          posted results). Missing parts stay None; a broken entry raises ParseError.
#          Only reads the outcome measures: numbers, groups and analyses are not kept.
#
# USE:     extraction = parse_results_outcomes(body, stored)
#          evidence = extract_results_evidence(body, stored)     # one Evidence per value present
##############################################################################

import json

from pydantic import ValidationError

from trialtrace.contracts import Evidence, ResultsExtraction, ResultsOutcome
from trialtrace.parse.registry_parser import ParseError, get_path, load_json
from trialtrace.sources.raw_store import StoredRaw

RESULTS_MEASURES_PATH = "resultsSection.outcomeMeasuresModule"

RESULT_TYPES = {                                         # the registry's type text -> our role
    "PRIMARY": "primary",
    "SECONDARY": "secondary",
    "OTHER_PRE_SPECIFIED": "other",
    "POST_HOC": "post_hoc",
}


def results_outcome_path(position: int, field: str) -> str:
    """Where one reported outcome field sits in the record (for evidence later)."""
    return f"{RESULTS_MEASURES_PATH}.outcomeMeasures.{position}.{field}"


def parse_results_outcomes(body: bytes, stored: StoredRaw) -> ResultsExtraction:
    """Reported outcomes of one saved record, in the registry's own order.

    status "absent" = no results measures posted (not the same as "no outcomes reported").
    A type we do not recognise becomes None (unknown), never a guess.
    """
    data = load_json(body)
    if get_path(data, "protocolSection.identificationModule.nctId") != stored.nct_id:
        raise ParseError(f"record is not {stored.nct_id!r}")

    module = get_path(data, RESULTS_MEASURES_PATH)
    if module is None:
        return ResultsExtraction(status="absent")
    if not isinstance(module, dict):
        raise ParseError("results outcome module is not an object")

    entries = module.get("outcomeMeasures")
    if entries is None:
        return ResultsExtraction(status="absent")
    if not isinstance(entries, list):
        raise ParseError("outcomeMeasures is not a list")

    outcomes = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ParseError(f"outcomeMeasures[{position}] is not an object")
        try:
            outcomes.append(ResultsOutcome(
                nct_id=stored.nct_id, content_hash=stored.content_hash, position=position,
                result_type=RESULT_TYPES.get(entry.get("type")),        # unknown or missing -> None
                measure_text=entry.get("title"),
                description=entry.get("description") or None,           # empty text = not given
                time_frame=entry.get("timeFrame") or None,
                population=entry.get("populationDescription") or None,
                reporting_status=entry.get("reportingStatus") or None,
            ))
        except ValidationError as exc:
            raise ParseError(f"outcomeMeasures[{position}] failed validation") from exc
    return ResultsExtraction(status="ok", outcomes=outcomes)


RESULTS_FIELDS = ("title", "type", "description", "timeFrame")     # the keys we keep evidence for


def results_evidence_ids(outcome: ResultsOutcome) -> list[str]:
    """Evidence IDs of the fields this reported outcome has (same address rule extract_results_evidence uses)."""
    prefix = f"{outcome.nct_id}:{outcome.content_hash[:12]}:"
    ids = [prefix + results_outcome_path(outcome.position, "title")]
    if outcome.result_type is not None:
        ids.append(prefix + results_outcome_path(outcome.position, "type"))
    if outcome.description:
        ids.append(prefix + results_outcome_path(outcome.position, "description"))
    if outcome.time_frame:
        ids.append(prefix + results_outcome_path(outcome.position, "timeFrame"))
    return ids


def extract_results_evidence(body: bytes, stored: StoredRaw) -> list[Evidence]:
    """One Evidence per reported-outcome value present in the record. Missing values get none.

    A type we do not recognise gets none either: the claim never rests on a value we could not read.
    """
    data = load_json(body)
    entries = get_path(data, f"{RESULTS_MEASURES_PATH}.outcomeMeasures")
    if not isinstance(entries, list):
        return []

    items = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        for field in RESULTS_FIELDS:
            value = entry.get(field)
            if value is None or value == "":
                continue
            if field == "type" and value not in RESULT_TYPES:
                continue
            path = results_outcome_path(position, field)
            items.append(Evidence(
                evidence_id=f"{stored.nct_id}:{stored.content_hash[:12]}:{path}",
                nct_id=stored.nct_id, content_hash=stored.content_hash, source="clinicaltrials",
                source_url=f"https://clinicaltrials.gov/study/{stored.nct_id}",
                field_path=path, value=value if isinstance(value, str) else json.dumps(value),
                retrieved_at=stored.retrieved_at,
            ))
    return items
