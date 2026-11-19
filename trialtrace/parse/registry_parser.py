##############################################################################
# trialtrace/parse/registry_parser.py
#
# PURPOSE: Turn one saved registry file (raw JSON) into a Snapshot plus the
#          Evidence behind it. A missing field stays None, never a guess.
#          A record we cannot trust raises ParseError, so the failure is
#          recorded by the caller instead of silently dropped.
#
# USE:     from trialtrace.parse.registry_parser import parse_study, extract_evidence
#          snapshot = parse_study(body, stored)       # stored comes from save_raw()
#          evidence = extract_evidence(body, stored)
##############################################################################

import json
from typing import Any

from pydantic import ValidationError

from trialtrace.contracts import Evidence, OutcomeExtraction, OutcomeRole, RegisteredOutcome, Snapshot
from trialtrace.sources.raw_store import StoredRaw


class ParseError(ValueError):
    """A source response could not be turned into our contracts."""


def get_path(data: Any, path: str) -> Any | None:
    """Follow 'a.b.0.c' down nested dicts and lists (a number picks a list item).
    Any missing step gives None."""
    current = data
    for key in path.split("."):
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        elif isinstance(current, list):
            # A list is entered by position: the key must be a plain number in range
            if not (key.isascii() and key.isdigit()) or int(key) >= len(current):
                return None
            current = current[int(key)]
        else:
            return None
    return current


OUTCOME_LISTS: dict[OutcomeRole, str] = {      # role -> key in the record, in priority order
    "primary": "primaryOutcomes",
    "secondary": "secondaryOutcomes",
    "other": "otherOutcomes",
}
OUTCOME_FIELDS = ("measure", "description", "timeFrame")                           # keys of one outcome entry


def outcome_path(role: OutcomeRole, position: int, field: str) -> str:
    """Where one outcome field sits in the record. Evidence for it uses exactly this path."""
    return f"protocolSection.outcomesModule.{OUTCOME_LISTS[role]}.{position}.{field}"


def load_json(body: bytes) -> Any:
    """Bytes -> Python. Bad JSON is a ParseError, not a crash."""
    try:
        return json.loads(body)
    except ValueError as exc:               # JSONDecodeError and bad UTF-8 are both ValueErrors
        raise ParseError("response is not valid JSON") from exc


def parse_study(body: bytes, stored: StoredRaw) -> Snapshot:
    """Raw registry JSON -> Snapshot. Raises ParseError if the record is not usable."""
    data = load_json(body)

    # The record must be the trial we asked for, or we would store the wrong trial
    nct_id = get_path(data, "protocolSection.identificationModule.nctId")
    if nct_id != stored.nct_id:
        raise ParseError(f"expected {stored.nct_id!r}, record says {nct_id!r}")

    completion_type = get_path(data, "protocolSection.statusModule.primaryCompletionDateStruct.type")
    has_results = get_path(data, "hasResults")

    try:
        return Snapshot(
            nct_id=nct_id,
            title=get_path(data, "protocolSection.identificationModule.briefTitle"),
            sponsor=get_path(data, "protocolSection.sponsorCollaboratorsModule.leadSponsor.name"),
            overall_status=get_path(data, "protocolSection.statusModule.overallStatus"),
            primary_completion_date=get_path(data, "protocolSection.statusModule.primaryCompletionDateStruct.date"),
            registry_last_update=get_path(data, "protocolSection.statusModule.lastUpdatePostDateStruct.date"),
            # An unexpected type becomes None (unknown), not a crash and not a guess
            primary_completion_type=completion_type if completion_type in ("ACTUAL", "ESTIMATED") else None,
            # Only a real true/false counts; anything else (missing, "yes", 1) is unknown
            has_results=has_results if isinstance(has_results, bool) else None,
            content_hash=stored.content_hash,
            source_url=f"https://clinicaltrials.gov/study/{nct_id}",
            raw_file_path=stored.raw_file_path,
            retrieved_at=stored.retrieved_at,
        )
    except ValidationError as exc:
        raise ParseError(f"record failed validation: {exc.error_count()} problem(s)") from exc


# Every value the Snapshot shows, by its exact location in the raw JSON
EVIDENCE_PATHS = (
    "protocolSection.identificationModule.briefTitle",
    "protocolSection.sponsorCollaboratorsModule.leadSponsor.name",
    "protocolSection.statusModule.overallStatus",
    "protocolSection.statusModule.primaryCompletionDateStruct.date",
    "protocolSection.statusModule.primaryCompletionDateStruct.type",
    "protocolSection.statusModule.lastUpdatePostDateStruct.date",
    "hasResults",
)


def parse_outcomes(body: bytes, stored: StoredRaw) -> OutcomeExtraction:
    """Registered outcomes of one saved record, in the registry's own order.

    status "absent" = the record has no outcomes section. status "ok" with an empty
    list = the section is there but lists nothing. A malformed entry raises ParseError.
    """
    data = load_json(body)
    if get_path(data, "protocolSection.identificationModule.nctId") != stored.nct_id:
        raise ParseError(f"record is not {stored.nct_id!r}")

    module = get_path(data, "protocolSection.outcomesModule")
    if module is None:
        return OutcomeExtraction(status="absent")
    if not isinstance(module, dict):
        raise ParseError("outcomesModule is not an object")

    outcomes = []
    for role, key in OUTCOME_LISTS.items():
        entries = module.get(key)
        if entries is None:                                 # no list for this role: nothing to read
            continue
        if not isinstance(entries, list):
            raise ParseError(f"{key} is not a list")
        for position, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ParseError(f"{key}[{position}] is not an object")
            try:
                outcomes.append(RegisteredOutcome(
                    nct_id=stored.nct_id, content_hash=stored.content_hash,
                    role=role, position=position,
                    measure_text=entry.get("measure"),
                    description=entry.get("description") or None,     # empty text = not given
                    time_frame=entry.get("timeFrame") or None,
                ))
            except ValidationError as exc:
                raise ParseError(f"{key}[{position}] failed validation") from exc
    return OutcomeExtraction(status="ok", outcomes=outcomes)


def outcome_evidence_paths(data: Any) -> list[str]:
    """The address of every outcome field present in the record."""
    paths = []
    for role, key in OUTCOME_LISTS.items():
        entries = get_path(data, f"protocolSection.outcomesModule.{key}")
        if isinstance(entries, list):
            paths += [outcome_path(role, i, f) for i in range(len(entries)) for f in OUTCOME_FIELDS]
    return paths


def extract_evidence(body: bytes, stored: StoredRaw) -> list[Evidence]:
    """One Evidence per value present in the record. Missing values get none."""
    data = load_json(body)
    items = []

    for path in (*EVIDENCE_PATHS, *outcome_evidence_paths(data)):
        value = get_path(data, path)
        if value is None or value == "":
            continue                        # absence is not a value, so there is nothing to cite

        items.append(
            Evidence(
                # Same snapshot + same path = same ID, every time
                evidence_id=f"{stored.nct_id}:{stored.content_hash[:12]}:{path}",
                nct_id=stored.nct_id,
                content_hash=stored.content_hash,
                source="clinicaltrials",
                source_url=f"https://clinicaltrials.gov/study/{stored.nct_id}",
                field_path=path,
                # Exact source text: strings as-is, others as JSON text (true, false, 12)
                value=value if isinstance(value, str) else json.dumps(value),
                retrieved_at=stored.retrieved_at,
            )
        )

    return items
