##############################################################################
# trialtrace/compare/registry_changes.py
#
# PURPOSE: Question 5a: did the registered outcomes differ between two saved
#          versions of the same trial? Reports WHAT differs, with each version's
#          capture date and the registry's own update date.
#          Two kinds of result are kept apart:
#            changes      = clear differences (same outcome edited, or clearly added/removed)
#            needs_review = outcomes we could NOT match safely (never called a change)
#          Missing history or missing outcomes is "unknown", never "no change".
#
# PIPELINE (what compare_outcomes does, in order):
#   1. why_not_comparable  -> stop with "unknown" if a version or its outcomes are missing
#   2. group_by_title      -> group each version's outcomes by title (capitals/punctuation ignored)
#   3. for each title:     duplicate title      -> needs_review
#                          only in one version  -> removed / added (set aside for step 4)
#                          in both versions     -> compare_pair (role, time frame, description)
#   4. per role: if some outcomes were removed AND some added, they may be one outcome
#      reworded -> needs_review; otherwise the removals/additions are clear changes
#   5. build the RegistryChangeReport -> .to_finding()
#
# FROM THE DATABASE: check_registry_changes(conn, nct_id) loads the two most recently
#   seen different versions (get_recent_versions) with their outcomes (get_outcomes),
#   then runs compare_outcomes. One stored version = "earlier version not available".
#
# USE:     report = check_registry_changes(conn, "NCT04644237")      # from the database
#          report = compare_outcomes("NCT04644237", older, newer)    # from two versions you build
#          finding = report.to_finding()
##############################################################################

import re
from datetime import datetime
from difflib import SequenceMatcher             # finds which words differ between two texts
from typing import Literal

import psycopg
from pydantic import BaseModel, Field

from trialtrace.contracts import Finding, OutcomeExtraction, RegisteredOutcome, Snapshot
from trialtrace.db.repository import get_outcomes, get_recent_versions
from trialtrace.parse.registry_parser import outcome_path

ROLES = ("primary", "secondary", "other")


class ComparableVersion(BaseModel):
    """One saved version of a trial: its snapshot, its outcomes (None = never extracted),
    and the last time WE saw this version (from the observations table)."""

    snapshot: Snapshot
    outcomes: OutcomeExtraction | None
    captured_at: datetime


class OutcomeChange(BaseModel):
    """One difference, or one item that needs review."""

    kind: Literal["added", "removed", "role_changed", "time_frame_changed", "description_changed", "ambiguous"]
    measure_text: str                               # the outcome's registry text
    before: str | None = None                       # the older value (for added/removed: the role)
    after: str | None = None                        # the newer value
    evidence_ids: list[str] = Field(default_factory=list)


class RegistryChangeReport(BaseModel):
    nct_id: str
    status: Literal["changes", "needs_review", "no_changes", "unknown"]
    reason: str | None = None                       # why the status is unknown
    older_captured: datetime | None = None          # when WE downloaded each version
    newer_captured: datetime | None = None
    older_registry_update: str | None = None        # the registry's own date for each version
    newer_registry_update: str | None = None
    changes: list[OutcomeChange] = Field(default_factory=list)         # clear differences
    needs_review: list[OutcomeChange] = Field(default_factory=list)    # could not be matched safely
    compared_evidence_ids: list[str] = Field(default_factory=list)

    def to_finding(self) -> Finding:
        """The report as one Finding: a plain sentence plus the evidence it rests on."""
        limits = ["A difference between two captured versions does not show when or why the registry record was edited."]

        if self.status == "unknown":
            return Finding(
                nct_id=self.nct_id, check="registry_changes", category="unknown",
                claim="Whether registered outcomes changed between registry versions is unknown.",
                limitations=[self.reason or "Not enough information.", *limits],
            )

        span = (
            f"the version captured {self.older_captured:%Y-%m-%d} (registry update {self.older_registry_update or 'not given'}) "
            f"and the version captured {self.newer_captured:%Y-%m-%d} (registry update {self.newer_registry_update or 'not given'})"
        )

        if self.status == "no_changes":
            return Finding(
                nct_id=self.nct_id, check="registry_changes", category="no_registry_outcome_change",
                claim=f"The registered outcomes are the same in {span}.",
                evidence_ids=self.compared_evidence_ids, limitations=limits,
            )

        evidence = []
        review_text = ""
        for item in self.needs_review:
            evidence += item.evidence_ids
        if self.needs_review:
            phrases = [describe_review(item) for item in self.needs_review]
            review_text = "could not be matched safely: " + "; ".join(phrases) + ". They may be the same outcomes worded differently"

        if self.status == "needs_review":
            return Finding(
                nct_id=self.nct_id, check="registry_changes", category="registry_outcome_needs_review",
                claim=f"Outcomes between {span} {review_text}, so no change is confirmed.",
                evidence_ids=sorted(set(evidence)), limitations=[*limits, "Needs review."],
            )

        phrases = []
        for change in self.changes:
            phrases.append(describe(change))
            evidence += change.evidence_ids
        claim = f"{len(self.changes)} registered outcome difference(s) between {span}: " + "; ".join(phrases)
        if review_text:
            claim += f". Separately, some outcomes {review_text}"
        return Finding(
            nct_id=self.nct_id, check="registry_changes", category="registry_outcome_change",
            claim=claim + ".", evidence_ids=sorted(set(evidence)), limitations=[*limits, "Differences are for review."],
        )


def describe(change: OutcomeChange) -> str:
    """One short phrase for one clear difference."""
    title = f'"{change.measure_text}"'
    if change.kind == "added":
        return f"{title} is listed as {change.after} in the newer version but not in the older"
    if change.kind == "removed":
        return f"{title} was listed as {change.before} in the older version but not in the newer"
    if change.kind == "role_changed":
        return f"{title} moved from {change.before} to {change.after}"
    if change.kind == "time_frame_changed":
        return f"{title} time frame differs: {show_difference(change.before, change.after)}"
    return f"{title} description differs: {show_difference(change.before, change.after)}"


def describe_review(item: OutcomeChange) -> str:
    """One short phrase for one item that could not be matched safely."""
    title = f'"{item.measure_text}"'
    if item.kind == "added":
        return f"{title} ({item.after}, newer) has no title match in the older version"
    if item.kind == "removed":
        return f"{title} ({item.before}, older) has no title match in the newer version"
    return f"{title} appears more than once, so it cannot be matched"


def shorten(text: str, limit: int = 80) -> str:
    """Cut a long text to `limit` characters, so one sentence stays readable."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def show_difference(before: str | None, after: str | None, context: int = 3) -> str:
    """Show which words changed, e.g. 'Time to [death → progression]'.

    Changed words are marked in [old → new] and only `context` words around them are kept.
    """
    old_words = (before or "").split()
    new_words = (after or "").split()
    if not old_words:
        return f"(not given) → {shorten(' '.join(new_words))}"
    if not new_words:
        return f"{shorten(' '.join(old_words))} → (not given)"

    # Step 1: walk through both texts, producing one token per unchanged word or per changed stretch
    tokens = []                                     # each token is (text, changed?)
    matcher = SequenceMatcher(None, old_words, new_words)
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            for word in old_words[old_start:old_end]:
                tokens.append((word, False))
        else:
            old_part = " ".join(old_words[old_start:old_end]) or "(nothing)"
            new_part = " ".join(new_words[new_start:new_end]) or "(nothing)"
            tokens.append((f"[{old_part} → {new_part}]", True))

    # Step 2: keep only the tokens within `context` words of a change
    keep = [False] * len(tokens)
    for index, (text, changed) in enumerate(tokens):
        if changed:
            for near in range(max(0, index - context), min(len(tokens), index + context + 1)):
                keep[near] = True

    # Step 3: join the kept tokens, writing "..." where words were left out
    pieces = []
    left_out = False
    for index, (text, changed) in enumerate(tokens):
        if keep[index]:
            pieces.append(text)
            left_out = False
        elif not left_out:
            pieces.append("...")
            left_out = True
    return " ".join(pieces)


def clean(text: str | None) -> str | None:
    """Collapse extra spaces and line breaks, so only a real wording difference counts."""
    if text is None:
        return None
    return " ".join(text.split())


def title_key(title: str) -> str:
    """The key used to decide two titles are the same outcome: ignores capitals and punctuation."""
    letters_and_spaces = re.sub(r"[^\w\s]", " ", title.lower())     # punctuation becomes a space
    return " ".join(letters_and_spaces.split())


def evidence_ids(outcome: RegisteredOutcome) -> list[str]:
    """Evidence IDs of the fields this outcome has (same address rule the parser uses)."""
    prefix = f"{outcome.nct_id}:{outcome.content_hash[:12]}:"
    ids = [prefix + outcome_path(outcome.role, outcome.position, "measure")]
    if outcome.description:
        ids.append(prefix + outcome_path(outcome.role, outcome.position, "description"))
    if outcome.time_frame:
        ids.append(prefix + outcome_path(outcome.role, outcome.position, "timeFrame"))
    return ids


def group_by_title(outcomes: list[RegisteredOutcome]) -> dict[str, list[RegisteredOutcome]]:
    """{title key: [outcomes with that title]}. Usually one each; two means we cannot match safely."""
    groups: dict[str, list[RegisteredOutcome]] = {}
    for outcome in outcomes:
        key = title_key(outcome.measure_text)
        if key not in groups:
            groups[key] = []
        groups[key].append(outcome)
    return groups


def compare_pair(old: RegisteredOutcome, new: RegisteredOutcome) -> list[OutcomeChange]:
    """Differences between the same outcome in the older and newer version."""
    changes = []
    ids = evidence_ids(old) + evidence_ids(new)

    if old.role != new.role:
        changes.append(OutcomeChange(kind="role_changed", measure_text=new.measure_text, before=old.role, after=new.role, evidence_ids=ids))
    if clean(old.time_frame) != clean(new.time_frame):
        changes.append(OutcomeChange(kind="time_frame_changed", measure_text=new.measure_text, before=old.time_frame, after=new.time_frame, evidence_ids=ids))
    if clean(old.description) != clean(new.description):
        changes.append(OutcomeChange(kind="description_changed", measure_text=new.measure_text, before=old.description, after=new.description, evidence_ids=ids))
    return changes


def why_not_comparable(older: ComparableVersion | None, newer: ComparableVersion) -> str | None:
    """None if the two versions can be compared; otherwise the reason they cannot."""
    if older is None:
        return "An earlier version is not available."
    for name, version in (("older", older), ("newer", newer)):
        if version.outcomes is None:
            return f"Outcomes were not extracted for the {name} version."
        if version.outcomes.status == "absent":
            return f"The {name} version has no outcomes section."
    return None


def check_registry_changes(conn: psycopg.Connection, nct_id: str) -> RegistryChangeReport:
    """Question 5a for one stored trial: compare its two most recently seen different versions."""
    recent = get_recent_versions(conn, nct_id, limit=2)        # newest first
    if not recent:
        return RegistryChangeReport(nct_id=nct_id, status="unknown", reason="This trial is not stored.")

    versions = []
    for snapshot, last_seen in recent:
        outcomes = get_outcomes(conn, nct_id, snapshot.content_hash)
        versions.append(ComparableVersion(snapshot=snapshot, outcomes=outcomes, captured_at=last_seen))

    newer = versions[0]
    older = versions[1] if len(versions) > 1 else None         # one stored version = no earlier one
    return compare_outcomes(nct_id, older, newer)


def compare_outcomes(nct_id: str, older: ComparableVersion | None, newer: ComparableVersion) -> RegistryChangeReport:
    """Compare the registered outcomes of two saved versions of one trial."""
    if older is not None and older.snapshot.content_hash == newer.snapshot.content_hash:
        raise ValueError("these are the same saved version; compare two different versions")

    reason = why_not_comparable(older, newer)
    if reason is not None:
        return RegistryChangeReport(
            nct_id=nct_id, status="unknown", reason=reason,
            older_captured=older.captured_at if older else None,
            newer_captured=newer.captured_at,
            older_registry_update=older.snapshot.registry_last_update if older else None,
            newer_registry_update=newer.snapshot.registry_last_update,
        )

    # why_not_comparable already guaranteed this; the line also tells the editor's type checker
    assert older is not None and older.outcomes is not None and newer.outcomes is not None

    old_groups = group_by_title(older.outcomes.outcomes)
    new_groups = group_by_title(newer.outcomes.outcomes)

    # Every title, older ones first, then titles that only the newer version has
    titles = list(old_groups)
    for title in new_groups:
        if title not in old_groups:
            titles.append(title)

    changes = []
    needs_review = []
    removed = []
    added = []
    for title in titles:
        old_list = old_groups.get(title, [])
        new_list = new_groups.get(title, [])

        if len(old_list) > 1 or len(new_list) > 1:
            ids = []
            for outcome in old_list + new_list:
                ids += evidence_ids(outcome)
            needs_review.append(OutcomeChange(kind="ambiguous", measure_text=(old_list + new_list)[0].measure_text, evidence_ids=ids))
        elif not new_list:
            removed.append(OutcomeChange(kind="removed", measure_text=old_list[0].measure_text, before=old_list[0].role, evidence_ids=evidence_ids(old_list[0])))
        elif not old_list:
            added.append(OutcomeChange(kind="added", measure_text=new_list[0].measure_text, after=new_list[0].role, evidence_ids=evidence_ids(new_list[0])))
        else:
            changes += compare_pair(old_list[0], new_list[0])

    # In one role, an outcome that vanished next to one that appeared may be the same outcome
    # reworded: set both aside for review. A removal or addition on its own is a clear change.
    for role in ROLES:
        removed_here = [c for c in removed if c.before == role]
        added_here = [c for c in added if c.after == role]
        if removed_here and added_here:
            needs_review += removed_here + added_here
        else:
            changes += removed_here + added_here

    compared = []
    for outcome in older.outcomes.outcomes + newer.outcomes.outcomes:
        compared += evidence_ids(outcome)

    if changes:
        status = "changes"
    elif needs_review:
        status = "needs_review"
    elif compared:
        status = "no_changes"
    else:
        status, reason = "unknown", "Neither version lists any outcomes."

    return RegistryChangeReport(
        nct_id=nct_id, status=status, reason=reason, changes=changes, needs_review=needs_review,
        compared_evidence_ids=compared,
        older_captured=older.captured_at, newer_captured=newer.captured_at,
        older_registry_update=older.snapshot.registry_last_update, newer_registry_update=newer.snapshot.registry_last_update,
    )
