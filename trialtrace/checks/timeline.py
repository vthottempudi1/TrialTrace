##############################################################################
# trialtrace/checks/timeline.py
#
# PURPOSE: Question 2: was the trial's ACTUAL primary completion more than
#          12 months before `as_of`? Answers "yes", "no", "not sure" or
#          "estimated date". A partial date ("2019-05") never gets an invented
#          day, and bad input gives "not sure", never a crash.
#          Says only how long ago the registry's date is. It does not say
#          whether results were due.
# CONVENTION: the anniversary of 29 Feb in a non-leap year is 1 March.
#
# USE:     from trialtrace.checks.timeline import check_timeline
#          finding = check_timeline("NCT04644237", snapshot, evidence, date.today())
##############################################################################

from calendar import monthrange                 # monthrange(2024, 2) -> (weekday, 29): days in that month
from datetime import date

from trialtrace.contracts import Evidence, Finding, Snapshot

DATE_PATH = "protocolSection.statusModule.primaryCompletionDateStruct.date"
TYPE_PATH = "protocolSection.statusModule.primaryCompletionDateStruct.type"


def possible_days(raw: str) -> tuple[date, date] | None:
    """'2019-05-14' -> that day twice. '2019-05' -> 1 May and 31 May. Bad input -> None."""
    try:
        parts = [int(p) for p in raw.split("-")]
        if len(parts) == 3:
            day = date(*parts)
            return day, day
        if len(parts) == 2:
            year, month = parts
            return date(year, month, 1), date(year, month, monthrange(year, month)[1])
    except ValueError:                          # "2019-13", "abc", "2019-02-30"
        return None
    return None                                 # year only: too vague


def one_year_later(day: date) -> date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:                          # 29 Feb in a non-leap year
        return date(day.year + 1, 3, 1)


def older_than_12_months(raw: str | None, date_type: str | None, as_of: date) -> str:
    if date_type == "ESTIMATED":
        return "estimated date"                 # a planned date, not a confirmed completion
    if date_type != "ACTUAL":
        return "not sure"                       # type missing or unexpected
    days = possible_days(raw) if raw else None
    if days is None:
        return "not sure"
    earliest, latest = days
    if one_year_later(latest) < as_of:          # even the latest possible day is over a year ago
        return "yes"
    if one_year_later(earliest) >= as_of:       # even the earliest possible day is within a year
        return "no"
    return "not sure"                           # the month straddles the line


# Answer from older_than_12_months -> Finding category
CATEGORIES = {
    "yes": "over_12_months",
    "no": "within_12_months",
    "estimated date": "estimated_date",
    "not sure": "unknown",
}


def check_timeline(nct_id: str, snapshot: Snapshot | None, evidence: list[Evidence], as_of: date) -> Finding:
    """Registry completion date -> one Finding, citing the date and its type."""

    def unknown(reason: str) -> Finding:
        return Finding(
            nct_id=nct_id, check="timeline", category="unknown",
            claim="How long ago the trial's primary completion was is unknown.",
            limitations=[reason],
        )

    if snapshot is None:
        return unknown("No stored registry record: never ingested, or every fetch failed.")

    answer = older_than_12_months(snapshot.primary_completion_date, snapshot.primary_completion_type, as_of)
    category = CATEGORIES[answer]
    if category == "unknown":
        return unknown("The completion date is missing, malformed, too vague, or the month straddles the 12-month line.")

    # Cite both the date and its type, taken from THIS snapshot's file
    own = [
        e for e in evidence
        if e.content_hash == snapshot.content_hash and e.field_path in (DATE_PATH, TYPE_PATH)
    ]
    if {e.field_path for e in own} != {DATE_PATH, TYPE_PATH}:    # both fields covered, not just any two rows
        return unknown("The completion date or its type has no stored evidence.")
    cited = [e.evidence_id for e in own]

    claims = {
        "over_12_months": "more than 12 months before",
        "within_12_months": "12 months or less before",
    }
    return Finding(
        nct_id=nct_id, check="timeline", category=category,
        claim=(
            f"The registry's primary completion date ({snapshot.primary_completion_date}, "
            f"{snapshot.primary_completion_type}) is {claims[category]} {as_of:%Y-%m-%d}."
            if category != "estimated_date" else
            f"The registry's primary completion date ({snapshot.primary_completion_date}) "
            f"is an estimate, not a confirmed completion."
        ),
        evidence_ids=cited,
        limitations=[
            "Reflects the registry record at retrieval time.",
            "Says nothing about whether results were due; no deadline is assessed.",
        ],
    )
