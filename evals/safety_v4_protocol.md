# Safety v4: independent reference protocol

Tests reader v6/helper v3, including the product's registry-aware identity check. No full text, agents or baseline-superiority requirement. v2/v3 are development data.

## Review order

Venu or another reviewer independently labels BLIND_SUBSET.md (20 cases) BEFORE seeing any AI draft references. None have been drafted. Read every sentence, including empty abstracts. Finish all other reference files and record any AI assistance outside that subset. Check candidate IDs, population and windows, not only counts. Record the actual reviewer/date and actual independent-subset completion in reference_review.json. Do not enter a person's name without their work.

Validate complete references; extend in saved order if fewer than 60 strict target-attributable statements and reserve trials remain. Then freeze sources, labels, code, model/settings and dependencies; run once. No output-driven label changes or replacement cases. Pre-run reference acceptance is not a post-run match approval.

## Separate sets

Naturalistic: new trial IDs from a fixed metadata-only external draw. Production selection is first ten numeric PMIDs; prior papers/text are coverage-only. Report paper/trial coverage and skipped papers.

Targeted: 40 constructed cases: ten agreement, ten disagreement, and four each for population, identity, event scope, measure and granularity. All identifiers are fictional placeholders. These are designed with knowledge of the implementation; independent labels do not turn them into real-world evidence. Never pool their scores with naturalistic estimates.

## Source statements and attribution

Inventory explicit participant counts, percentages or none for deaths, serious-AE totals and named harms/classes. Include complications, eligible age subgroups and severity-restricted harms. Exclude baseline characteristics, approximate/bounded/range numbers, occurrences of events, unnamed AEs, withdrawal counts and mean changes. Exclude disease outcomes specifically studied as efficacy/prevention outcomes, except deaths remain eligible. A result described as a benefit is not automatically excluded.

Include eligible source statements even if attributable to another trial, pooled trials or unresolved identity, and mark that attribution. This separates source support from target-trial correctness. The reader is not expected to extract every other-trial claim; source-inventory coverage is descriptive, not a recall acceptance gate. Non-results documents have statements=[].

Copy numbers only. A denominator must belong to the statement/event context, not enrolment, another event clause or an intervening relative subgroup. Never calculate missing values. One reported event is not automatically one affected person. Explicit time-to-event percentages are estimates, marked time_to_event; they cannot compare with raw adverse-event-table counts.

## Granularity and population

Retain explicit totals and separately numbered cause-specific details. Link overlapping statements with claim_family; they are not independent clinical observations and must not be added. Keep a combined class as one statement unless separate component numbers are given. Cause null does not establish all-cause death.

Arm means treatment/dose/course. Population means AYAs, non-AYAs or another restriction. Keep both. An arm match does not verify population identity. Candidate IDs are separately equivalent alternatives, never pieces to combine. A broad event cannot match one seriousness stratum without source evidence.

## Reference format

Each references.jsonl has one blank JSON record per case. Replace null document/statements with real labels; statements=[] means an explicitly empty inventory. Keep case IDs. The exact schema is in evals/safety_v4_reference.py; unknown fields are rejected.

Each statement requires: sentence, quote, kind, term, arm, population, count, denominator, percent, grade, treatment_related, cause, all_cause, measure, event_scope, detail_level, claim_family, attribution, category, acceptable_arm_ids, acceptable_event_ids, window, population_relation, abstract_window_quote, registry_window_quote. Use null for unstated optional values; false only for an unsupported positive claim such as unstated relatedness/all-cause. aliases is an optional dictionary of prelisted exact acceptable term/arm/population/grade/cause wording; no numeric aliases.

Syntax example only, not an answer to any case:

```json
{"case_id":"EXAMPLE","document":"trial_results","notes":"Reviewer explanation","statements":[{"sentence":1,"quote":"Serious adverse events affected 6 of 120 Drug participants.","kind":"serious","term":null,"arm":"Drug","population":null,"count":6,"denominator":120,"percent":null,"grade":null,"treatment_related":false,"cause":null,"all_cause":false,"measure":"participant_proportion","event_scope":"unspecified","detail_level":"unspecified","claim_family":"s1-serious-drug","attribution":"target","category":"safety_unclear","acceptable_arm_ids":["arm:EG0"],"acceptable_event_ids":[],"window":"unknown","population_relation":"unknown","abstract_window_quote":null,"registry_window_quote":null,"aliases":{}}]}
```

quote must be exact in the numbered source sentence. Attribution is target/other/pooled/unresolved; category is required for target and null otherwise. A registration field cannot settle a contradictory design/population. event_scope is serious/non_serious/all/unspecified; detail_level is total/component/unspecified. Both are based on the source. Window and population_relation are same/different/unknown. Known windows require exact supporting safety-collection quotes from both sources, not median follow-up or an analysis time point. A same/different population relation also needs abstract_population_quote and registry_population_quote from the abstract and the identified registry arm title/description. Equal denominators alone are insufficient; use null/omit these quotes only when population_relation is unknown.

## Category order

Definition/measure/collection scope, arm, counterpart, population/denominator, window, then numbers. Confirmed scope differences or missing denominator: not comparable. Unresolved arm/event: unclear. Missing death/serious data for a known group: in-paper/not-registry; absent named-event correspondence: unclear. Unverified window/population: unclear when other required numeric inputs exist. Numeric verdicts require same population, equivalent safety window and denominator. Equal N alone is not proof; counts must match exactly and percent tolerance is one point. Never add registry rows/groups.

## Scoring and acceptance

Primary naturalistic scores use strict unseen target-attributable extraction. Source-support precision requires correct source fact fields, not only a quoted main number (detail-role metadata is scored separately). A quoted other-trial claim cannot inflate target precision. Fully correct target recall/precision are also reported against all target labels/predictions, rather than only matched statements. Score population, cause/all-cause, measure, event scope and detail role as well as the original fields. One-to-one matching uses type, sentence, event aliases and main number; all-field agreement breaks deterministic ties. It never matches on category or candidate IDs. Report claim-family counts and clustering limitations.

Report unreviewed categories, unsupported consistent claims, helper candidate/window accuracy, abstentions/failures, difference discovery plus routing and empty cases. Actual human reviews after output can be scored separately from saved packets/logs. Never inject gold scope or aliases as a human approval.

Targets, applied separately per set: target extraction recall/precision >=95%; all-field accuracy >=95%; window assertions >=95% correct with at least 10; no wrong helper proposals with at least 30; no false consistent with at least 10 consistent predictions; every difference discovered and routed with at least 10 labelled differences. An observed error fails a zero-error rule even below the minimum. Absent branches are not tested. Constructed success cannot replace missing real branches.

Unreviewed outputs may make no verified numeric claims. That does not validate numeric-report capability; an observed human review exercise is needed. Automated scores do not establish human resolution accuracy/time or clinical certification. Statement-level intervals do not adjust for trial clustering or establish rare-error guarantees.

The reader/helper production sources and settings are locked in system_candidate.json before any labels. They match the prior D64 repair configuration. Changing that system after seeing these references invalidates this test; do not simply freeze a newly tuned system on the same labels. Record the independent subset author/date separately from full-reference review. Plain independent notes may be formatted into JSON later, but semantic decisions must remain the reviewer's own.
