##############################################################################
# trialtrace/safety/safety_registry.py
#
# PURPOSE: Safety, step S4: say what the registry's adverse-event table shows for one trial, with evidence.
#          Per treatment group: deaths, serious adverse events, and the most frequent serious events.
#          Descriptive only: nothing is compared with a paper and nothing is judged. Plain code, no LLM.
#          Rules:
#            - a number the record does not give is "not given" (unknown), never 0
#            - groups are never added up; a group that looks pooled is labelled
#            - no stored evidence, no claim: the answer is then "unknown"
#          cited_values is the ONE list of values the finding rests on; the evidence rows and the
#          finding's evidence IDs are both built from it, so they can never drift apart.
#
# USE:     evidence = safety_evidence(safety, stored.retrieved_at)                  # at ingest
#          finding = check_safety_registry(nct_id, snapshot, safety, evidence)      # in the report
##############################################################################

from datetime import datetime

from trialtrace.contracts import Evidence, Finding, SafetyArm, SafetyEvent, SafetyEventCount, SafetyExtraction, Snapshot
from trialtrace.safety.safety_parser import safety_path

TOP_EVENTS = 3                          # how many serious events are named per group

# SafetyArm field -> the key in the registry record
ARM_COUNT_KEYS = {
    "deaths_affected": "deathsNumAffected", "deaths_at_risk": "deathsNumAtRisk",
    "serious_affected": "seriousNumAffected", "serious_at_risk": "seriousNumAtRisk",
}


def count_text(affected: int | None, at_risk: int | None) -> str:
    """'42 of 101', or what is missing in plain words."""
    if affected is None:
        return "not given"
    if at_risk is None:
        return f"{affected} (number at risk not given)"
    return f"{affected} of {at_risk}"


def arm_label(arm: SafetyArm) -> str:
    label = f'"{arm.title}"'
    if arm.looks_pooled:
        label += " (looks pooled)"
    return label


def top_serious_events(safety: SafetyExtraction, group_id: str) -> list[tuple[SafetyEvent, int, SafetyEventCount]]:
    """The serious events with the most participants affected in one group: (event, place of the count in the row, count).

    Only named events with a count above 0. Equal counts keep the registry's order.
    """
    found = []
    for event in safety.events:
        if event.kind != "serious" or event.term is None:
            continue
        for index, count in enumerate(event.counts):
            if count.group_id == group_id and count.affected:
                found.append((event, index, count))
                break                               # one count per group per row
    found.sort(key=lambda item: item[2].affected, reverse=True)
    return found[:TOP_EVENTS]


def cited_values(safety: SafetyExtraction) -> list[tuple[str, str]]:
    """(path in the record, value as text) for every value the finding rests on."""
    values = []
    for arm in safety.arms:
        values.append((safety_path("eventGroups", arm.position, "title"), arm.title))
        for field, key in ARM_COUNT_KEYS.items():
            number = getattr(arm, field)
            if number is not None:
                values.append((safety_path("eventGroups", arm.position, key), str(number)))
        for event, index, count in top_serious_events(safety, arm.group_id):
            values.append((safety_path("seriousEvents", event.position, "term"), event.term))
            values.append((safety_path("seriousEvents", event.position, "stats", index, "numAffected"), str(count.affected)))
            if count.at_risk is not None:
                values.append((safety_path("seriousEvents", event.position, "stats", index, "numAtRisk"), str(count.at_risk)))

    unique = []                                     # one event can be in the top list of two groups
    for item in values:
        if item not in unique:
            unique.append(item)
    return unique


def safety_evidence(safety: SafetyExtraction | None, retrieved_at: datetime) -> list[Evidence]:
    """One Evidence per value the safety finding cites. No module or no groups gives none."""
    if safety is None or not safety.arms:
        return []
    nct_id = safety.arms[0].nct_id
    content_hash = safety.arms[0].content_hash
    items = []
    for path, value in cited_values(safety):
        items.append(Evidence(
            evidence_id=f"{nct_id}:{content_hash[:12]}:{path}",
            nct_id=nct_id, content_hash=content_hash, source="clinicaltrials",
            source_url=f"https://clinicaltrials.gov/study/{nct_id}",
            field_path=path, value=value, retrieved_at=retrieved_at,
        ))
    return items


def check_safety_registry(
    nct_id: str, snapshot: Snapshot, safety: SafetyExtraction | None, evidence: list[Evidence],
) -> Finding:
    """The registry's adverse-event numbers for the newest stored version -> one Finding."""

    def unknown(reason: str) -> Finding:
        return Finding(
            nct_id=nct_id, check="safety_registry", category="unknown",
            claim="What the registry's adverse-event data shows is unknown.",
            limitations=[reason],
        )

    if safety is None:
        return unknown("The adverse-event data was not extracted for this version.")
    if safety.status == "absent":
        return unknown("This version of the record has no adverse-event section. That does not mean the trial had no side effects.")
    if not safety.arms:
        return unknown("The adverse-event section lists no treatment groups.")

    # The evidence must be stored for THIS version; refuse a claim we cannot cite
    prefix = f"{nct_id}:{snapshot.content_hash[:12]}:"
    cited = [prefix + path for path, value in cited_values(safety)]
    stored_ids = {item.evidence_id for item in evidence}
    for evidence_id in cited:
        if evidence_id not in stored_ids:
            return unknown("The adverse-event values have no stored evidence (run backfill-safety).")

    deaths, serious, events = [], [], []
    for arm in safety.arms:
        label = arm_label(arm)
        deaths.append(f"{count_text(arm.deaths_affected, arm.deaths_at_risk)} in {label}")
        serious.append(f"{count_text(arm.serious_affected, arm.serious_at_risk)} in {label}")
        top = top_serious_events(safety, arm.group_id)
        if top:
            names = ", ".join(f"{event.term} ({count_text(count.affected, count.at_risk)})" for event, index, count in top)
        else:
            names = "none listed with a count above 0"
        events.append(f"{label}: {names}")

    claim = (
        f"The registry record retrieved {snapshot.retrieved_at:%Y-%m-%d} lists adverse-event data for {len(safety.arms)} group(s). "
        f"Deaths (all causes): {'; '.join(deaths)}. "
        f"Participants with a serious adverse event: {'; '.join(serious)}. "
        f"Most frequent serious events: {'; '.join(events)}."
    )
    limitations = [
        "Descriptive only: these are the registry's numbers; they are not compared with any paper.",
        "A number the record does not give is unknown, not zero.",
        "Serious is the registry's classification, not a severity grade.",
        "Reflects the registry record at retrieval time.",
    ]
    if any(arm.looks_pooled for arm in safety.arms):
        limitations.append("A group that looks pooled may repeat participants of the other groups; groups are never added up.")
    return Finding(
        nct_id=nct_id, check="safety_registry", category="safety_registry_summary",
        claim=claim, evidence_ids=cited, limitations=limitations,
    )
