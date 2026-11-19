##############################################################################
# trialtrace/safety/safety_parser.py
#
# PURPOSE: Read the adverse-event module (resultsSection.adverseEventsModule) of one saved registry file:
#          the treatment groups with their death / serious / other counts, and the listed events with counts
#          per group. Plain code, no LLM. Lessons from the real data (D43):
#            - a number the record does not give stays None (unknown), never 0
#            - duplicate event rows are all kept (the same term can be listed twice with different counts)
#            - the frequency threshold and assessment type are kept: they say what "not listed" can mean
#            - a row with no term (real case: only a note about deaths) is kept with term None
#            - vocabulary and terms are kept exactly as written (free text, about 90 spellings)
#            - a group titled total / overall / all / combined is flagged looks_pooled, never added or dropped
#          A record with no module is "absent" (not "no side effects"). A broken entry raises ParseError.
#
# USE:     extraction = parse_safety(body, stored)
##############################################################################

from pydantic import ValidationError

from trialtrace.contracts import SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction
from trialtrace.parse.registry_parser import ParseError, get_path, load_json
from trialtrace.sources.raw_store import StoredRaw

SAFETY_PATH = "resultsSection.adverseEventsModule"
POOLED_STARTS = ("total", "overall", "all ", "combined", "pooled")
ASSESSMENT = {"SYSTEMATIC_ASSESSMENT": "systematic", "NON_SYSTEMATIC_ASSESSMENT": "non_systematic"}
EVENT_LISTS = (("serious", "seriousEvents"), ("other", "otherEvents"))


def safety_path(*parts) -> str:
    """Where a safety value sits in the record, e.g. safety_path('eventGroups', 0, 'title')."""
    return ".".join([SAFETY_PATH] + [str(part) for part in parts])


def whole_number(value, where: str):
    """An integer, or None when the record does not give one. Anything else (text, true/false) is a parse error."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(f"{where} is not a whole number")
    return value


def as_float(value, where: str):
    """The frequency threshold: the registry writes it as text or a number, e.g. "5"."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ParseError(f"{where} is not a number") from exc


def read_arm(entry, position: int, stored: StoredRaw) -> SafetyArm:
    where = f"eventGroups[{position}]"
    if not isinstance(entry, dict):
        raise ParseError(f"{where} is not an object")
    title = entry.get("title")
    try:
        return SafetyArm(
            nct_id=stored.nct_id, content_hash=stored.content_hash, position=position,
            group_id=entry.get("id"), title=title, description=entry.get("description") or None,
            deaths_affected=whole_number(entry.get("deathsNumAffected"), where + ".deathsNumAffected"),
            deaths_at_risk=whole_number(entry.get("deathsNumAtRisk"), where + ".deathsNumAtRisk"),
            serious_affected=whole_number(entry.get("seriousNumAffected"), where + ".seriousNumAffected"),
            serious_at_risk=whole_number(entry.get("seriousNumAtRisk"), where + ".seriousNumAtRisk"),
            other_affected=whole_number(entry.get("otherNumAffected"), where + ".otherNumAffected"),
            other_at_risk=whole_number(entry.get("otherNumAtRisk"), where + ".otherNumAtRisk"),
            looks_pooled=isinstance(title, str) and title.strip().lower().startswith(POOLED_STARTS),
        )
    except ValidationError as exc:
        raise ParseError(f"{where} failed validation") from exc


def read_event(entry, kind: str, position: int, stored: StoredRaw) -> SafetyEvent:
    where = f"{kind} event [{position}]"
    if not isinstance(entry, dict):
        raise ParseError(f"{where} is not an object")
    stats = entry.get("stats") or []
    if not isinstance(stats, list):
        raise ParseError(f"{where} stats is not a list")
    counts = []
    for stat in stats:
        if not isinstance(stat, dict):
            raise ParseError(f"{where} has a stat that is not an object")
        try:
            counts.append(SafetyEventCount(
                group_id=stat.get("groupId"),
                affected=whole_number(stat.get("numAffected"), where + " numAffected"),
                at_risk=whole_number(stat.get("numAtRisk"), where + " numAtRisk"),
                events=whole_number(stat.get("numEvents"), where + " numEvents"),
            ))
        except ValidationError as exc:
            raise ParseError(f"{where} has a bad stat") from exc
    try:
        return SafetyEvent(
            nct_id=stored.nct_id, content_hash=stored.content_hash, kind=kind, position=position,
            term=entry.get("term") or None, notes=entry.get("notes") or None, organ_system=entry.get("organSystem") or None,
            vocabulary=entry.get("sourceVocabulary") or None,
            assessment_type=ASSESSMENT.get(entry.get("assessmentType")),    # unknown or missing -> None
            counts=counts,
        )
    except ValidationError as exc:
        raise ParseError(f"{where} failed validation") from exc


def parse_safety(body: bytes, stored: StoredRaw) -> SafetyExtraction:
    """The adverse-event module of one saved record, in the registry's own order."""
    data = load_json(body)
    if get_path(data, "protocolSection.identificationModule.nctId") != stored.nct_id:
        raise ParseError(f"record is not {stored.nct_id!r}")

    module = get_path(data, SAFETY_PATH)
    if module is None:
        return SafetyExtraction(status="absent")
    if not isinstance(module, dict):
        raise ParseError("adverse events module is not an object")

    groups = module.get("eventGroups") or []
    if not isinstance(groups, list):
        raise ParseError("eventGroups is not a list")
    arms = [read_arm(entry, position, stored) for position, entry in enumerate(groups)]

    events = []
    for kind, key in EVENT_LISTS:
        entries = module.get(key) or []
        if not isinstance(entries, list):
            raise ParseError(f"{key} is not a list")
        for position, entry in enumerate(entries):
            events.append(read_event(entry, kind, position, stored))

    return SafetyExtraction(
        status="ok", frequency_threshold=as_float(module.get("frequencyThreshold"), "frequencyThreshold"),
        time_frame=module.get("timeFrame") or None, description=module.get("description") or None,
        arms=arms, events=events,
    )
