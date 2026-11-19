##############################################################################
# trialtrace/contracts.py
#
# PURPOSE: The shape of our data, with rules. Creating an object with a bad
#          value fails right away, so bad data never spreads.
#
# THE MODELS, grouped by what they describe:
#          Evidence                      one exact value from a saved source file (what claims point to)
#          Snapshot                      one downloaded version of a trial's registry record
#          RegisteredOutcome, OutcomeExtraction
#                                        the outcomes the trial PLANNED (registry plan)
#          ResultsOutcome, ResultsExtraction
#                                        the outcomes the trial REPORTED in its posted results
#          SafetyArm, SafetyEventCount, SafetyEvent, SafetyExtraction
#                                        the adverse-event (side effect) data in the posted results
#          Finding, StoredFinding        one answer to one question, and the same answer as saved with its review
#
# RULES ALL OF THEM FOLLOW:
#          - a value the source does not give is None ("unknown"), never a made-up 0 or empty text
#          - text from a source is kept exactly as written
#          - a bad value stops the program right away instead of travelling on
#
# USE:     from trialtrace.contracts import Snapshot, Evidence
##############################################################################

from datetime import datetime
from typing import Literal                  # Literal["A", "B"]: only these exact values allowed

from pydantic import BaseModel, Field, HttpUrl, model_validator
# BaseModel: a class whose fields are checked on creation
# Field(pattern=...): the text must match a regular expression
# HttpUrl: must be a valid web address

NCT_ID_PATTERN = r"^NCT[0-9]{8}$"           # "NCT" + exactly 8 digits ([0-9], not \d: no foreign digits)
HASH_PATTERN = r"^[0-9a-f]{64}$"


class Evidence(BaseModel):
    """One exact value copied from a saved source file, so that any claim can point to its proof.

    Example: the registry field "hasResults" with the value "true", from the trial file saved on a given day.
    A Finding lists evidence_ids; nothing we say is allowed to rest on anything that is not Evidence.
    """

    evidence_id: str = Field(min_length=1)            # "<NCT>:<first 12 hash chars>:<field path>"
    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    content_hash: str = Field(pattern=HASH_PATTERN)   # which saved file this came from
    source: Literal["clinicaltrials", "pubmed"]
    source_url: HttpUrl
    field_path: str = Field(min_length=1)             # where in the file, e.g. "hasResults"
    value: str = Field(min_length=1)                  # the exact text from the source
    retrieved_at: datetime


class Snapshot(BaseModel):
    """The main facts of one version of a trial's ClinicalTrials.gov record, as downloaded on one day.

    A trial can have many snapshots over time (the registry edits records). Each snapshot is identified by
    the trial ID plus content_hash (a fingerprint of the saved file), so the same content is stored once and a
    changed record becomes a new snapshot. A fact the record does not give is None (unknown).
    """

    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    title: str = Field(min_length=1)
    sponsor: str | None = None                        # None = missing in the source
    overall_status: str = Field(min_length=1)         # str, not Literal: the registry adds new statuses
    primary_completion_date: str | None = None        # raw text: "2019-05" has no day to invent
    registry_last_update: str | None = None           # when the registry says this version was posted (raw text)
    primary_completion_type: Literal["ACTUAL", "ESTIMATED"] | None = None
    has_results: bool | None = None                   # True, False, or None = unknown
    content_hash: str = Field(pattern=HASH_PATTERN)
    source_url: HttpUrl
    raw_file_path: str = Field(min_length=1)
    retrieved_at: datetime


OutcomeRole = Literal["primary", "secondary", "other"]    # the three outcome lists the registry has, in priority order


class RegisteredOutcome(BaseModel):
    """One outcome the trial PLANNED to measure, as listed in the registry's plan in one saved version.

    Example: role "primary", measure_text "Overall survival", time_frame "24 months".
    The registry has three lists (primary, secondary, other); position is the place within its list.
    The text is kept exactly as written. Note the registry's list may have been edited after results were
    posted, so this is the plan as of this version, not necessarily the original plan.
    """

    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    content_hash: str = Field(pattern=HASH_PATTERN)   # which saved version it came from
    role: OutcomeRole
    position: int = Field(ge=0)                       # 0 = first in its list; with role, identifies the outcome
    measure_text: str = Field(min_length=1)           # the registry's "measure" text for this outcome, kept exactly
    description: str | None = None                    # None = not given in the record
    time_frame: str | None = None                     # None = not given in the record


class OutcomeExtraction(BaseModel):
    """All planned outcomes read from one saved version, plus whether the record had an outcomes section.

    status "ok"     = the section is present (its list can still be empty)
    status "absent" = the record has no outcomes section at all (this is not the same as "no outcomes")
    """

    status: Literal["ok", "absent"]                   # ok = section present (list may still be empty); absent = no section
    outcomes: list[RegisteredOutcome] = Field(default_factory=list)

    @model_validator(mode="after")
    def absent_means_no_outcomes(self):
        if self.status == "absent" and self.outcomes:
            raise ValueError("an absent outcomes section cannot have outcomes")
        return self


ResultsType = Literal["primary", "secondary", "other", "post_hoc"]   # post_hoc = NOT in the registered plan


class ResultsOutcome(BaseModel):
    """One outcome measure the trial REPORTED in the results it posted, as written in one saved version.

    This is the results side to compare with the plan (RegisteredOutcome). Only trials that posted results
    have these. result_type says how the registry labels it; "post_hoc" means it was added after the plan, so
    it is NOT one of the planned outcomes. population is who was analysed for this measure.
    """

    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    content_hash: str = Field(pattern=HASH_PATTERN)   # which saved version it came from
    position: int = Field(ge=0)                       # index in the results list; with the hash, identifies it
    result_type: ResultsType | None = None            # None = missing or a type we do not know (unknown)
    measure_text: str = Field(min_length=1)           # the results "title", kept exactly
    description: str | None = None                    # None = not given in the record
    time_frame: str | None = None
    population: str | None = None                     # who was analysed
    reporting_status: str | None = None               # e.g. POSTED, raw text


class ResultsExtraction(BaseModel):
    """All reported outcomes read from one saved version, plus whether results measures were posted at all.

    status "ok"     = a results measures section is present (its list can still be empty)
    status "absent" = no results measures were posted (a trial can have no results yet)
    """

    status: Literal["ok", "absent"]                   # ok = results measures section present; absent = none posted
    outcomes: list[ResultsOutcome] = Field(default_factory=list)

    @model_validator(mode="after")
    def absent_means_no_outcomes(self):
        if self.status == "absent" and self.outcomes:
            raise ValueError("an absent results section cannot have outcomes")
        return self


class SafetyArm(BaseModel):
    """One treatment group (arm) from the adverse-event table a trial posted in the registry.

    Each group has three headcounts, each as "affected out of at risk":
      deaths  = participants who died (any cause)
      serious = participants with at least one serious adverse event
      other   = participants with at least one non-serious adverse event above the listing cutoff
    Example: serious_affected=42, serious_at_risk=101 means 42 of 101 participants had a serious event.

    Rule: a number the record does not give is None (unknown), never 0. A real 0 stays 0.
    This is one group of one saved version of the record, kept exactly as the registry wrote it.
    """

    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    content_hash: str = Field(pattern=HASH_PATTERN)
    position: int = Field(ge=0)                       # index in eventGroups
    group_id: str = Field(min_length=1)               # the registry's own id, e.g. EG000
    title: str = Field(min_length=1)
    description: str | None = None
    deaths_affected: int | None = Field(default=None, ge=0)
    deaths_at_risk: int | None = Field(default=None, ge=0)
    serious_affected: int | None = Field(default=None, ge=0)
    serious_at_risk: int | None = Field(default=None, ge=0)
    other_affected: int | None = Field(default=None, ge=0)
    other_at_risk: int | None = Field(default=None, ge=0)
    looks_pooled: bool = False                        # True if the title starts with total / overall / all / combined / pooled: the group may repeat people already in other groups, so never add it to the others


class SafetyEventCount(BaseModel):
    """The numbers one listed event has for one treatment group (matched to an arm by group_id).

    affected = participants who had this event, at_risk = participants counted for it,
    events = how many times it occurred (one person can have it more than once; often not given).
    None = the record does not say.
    """

    group_id: str = Field(min_length=1)
    affected: int | None = Field(default=None, ge=0)
    at_risk: int | None = Field(default=None, ge=0)
    events: int | None = Field(default=None, ge=0)    # number of occurrences; often missing


class SafetyEvent(BaseModel):
    """One adverse event as listed in the registry table, with its numbers for each treatment group.

    kind says which list it came from: "serious" (serious adverse events) or "other" (non-serious events;
    the registry only lists those above the frequency threshold). The same term can appear twice in one list
    with different numbers; both rows are kept.
    """

    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    content_hash: str = Field(pattern=HASH_PATTERN)
    kind: Literal["serious", "other"]
    position: int = Field(ge=0)                       # index within its own list (seriousEvents or otherEvents)
    term: str | None = None                           # kept exactly; may be a whole sentence; None = the row has no term (real case: only notes)
    notes: str | None = None                          # the registry's free-text note on the row
    organ_system: str | None = None
    vocabulary: str | None = None                     # free text as given, e.g. "MedDRA 25.1"
    assessment_type: Literal["systematic", "non_systematic"] | None = None
    counts: list[SafetyEventCount] = Field(default_factory=list)


class SafetyExtraction(BaseModel):
    """Everything the registry's adverse-event section says, read from one saved version of a trial.

    status "ok"     = the trial posted adverse-event data (it can still be empty)
    status "absent" = the record has no adverse-event section; this does NOT mean the trial had no side effects
    The "unknown" case (never read) is not a status here: the database gives None for that.
    """

    status: Literal["ok", "absent"]
    frequency_threshold: float | None = None          # percent of participants; a non-serious event below this is NOT listed (0 = every event listed; None = not given)
    time_frame: str | None = None
    description: str | None = None
    arms: list[SafetyArm] = Field(default_factory=list)
    events: list[SafetyEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def absent_means_empty(self):
        if self.status == "absent" and (self.arms or self.events):
            raise ValueError("an absent safety module cannot have arms or events")
        return self


class Finding(BaseModel):
    """One answer to one question about one trial: a short plain claim, its proof, and what it does not say.

    category is the answer itself, for example "results_available", "reported_outcomes_match",
    "registry_outcome_change" or "unknown".
    Rules: a claim must cite at least one piece of evidence, and only "unknown" may come without any, because
    "we could not tell" has nothing to point to. A finding describes what the sources say and where a person
    should look; it never says anyone did something wrong. Every finding is provisional until a person reviews it.
    """

    nct_id: str = Field(pattern=NCT_ID_PATTERN)
    check: str = Field(min_length=1)                  # which question: "results_posted", "timeline"...
    category: str = Field(min_length=1)               # the answer; "unknown" when we cannot say
    claim: str = Field(min_length=1)                  # one plain sentence, no legal words
    evidence_ids: list[str] = Field(default_factory=list)   # Evidence.evidence_id values the claim rests on
    limitations: list[str] = Field(default_factory=list)    # what this answer does NOT say
    comparison_arm: Literal["results_submission", "full_text", "abstract"] | None = None   # what was compared against the registry; None = not a registry-vs-reported comparison
    match_method: Literal["deterministic", "semantic"] | None = None   # how outcomes were matched (LLM-assisted = semantic)
    source_run_id: str | None = None                  # the paper-match run a paper finding was built from (audit link)
    evidence_completeness: Literal["complete", "incomplete"] | None = None   # paper summaries: were all listed papers read and usable? None = not a paper summary

    @model_validator(mode="after")                    # runs after the fields above pass their checks
    def claims_need_evidence(self):
        # No source value, no claim: only "unknown" may come without evidence
        if self.category != "unknown" and not self.evidence_ids:
            raise ValueError("a claim needs at least one evidence id")
        return self


class StoredFinding(BaseModel):
    """A finding as saved in one report run, together with the newest human decision about it (if any).

    review_decision is None until a person reviews it: "approved", "edited" (with edited_claim), "rejected" or
    "unclear" (the last two need a reason). The original finding is never changed by a review; decisions are
    kept as history and the newest one is shown here.
    """

    finding_id: str
    run_id: str
    finding: Finding
    review_decision: Literal["approved", "edited", "rejected", "unclear"] | None = None   # None = not reviewed yet
    reviewer: str | None = None
    review_reason: str | None = None
    edited_claim: str | None = None
