# Evaluation sets: what each one is and how it may be used

Status on 2026-11-13. Rule for every set: write the rule first, freeze the labels by hash, run once, report the result even if targets are missed. A set that has been run, audited or used to tune the system is **development data** and is never presented as unseen again.

## Outcome arm (do registered outcomes appear in paper abstracts?)

| Folder | What | Result | Use now |
|---|---|---|---|
| `v2/`, `v3/`, `v4/` | single-paper rounds, LLM vs rules baseline (D26b to D26r) | LLM finds far more reported outcomes; a few overclaims on unclear cases | history |
| `multipaper/` | 7 pilot trials, several papers each (D40) | pilot | history |
| `final/` | 19 trials, 204 labelled pairs, one frozen run (D42) | outcome recall 22/26, precision 22/26; baseline 8/26; 30 pairs checked blind by a person (27/30) | the main outcome-arm result |
| `multipaper_check/` | 13 trials (12 new; NCT03566667 also in `final/`, D91), 144 pairs, one fresh run (D84, 2026-11-13) | outcome recall 22/22, precision 22/27 without the duplicate; all 5 overclaims reached review, 0 failures, 0 bad quotes | smoke check only: labels drafted by one AI, not human-verified, not pooled with D40/D42 |
| `runs/overclaim_audit.json`, `runs/overclaim_fix_scores.json` | overclaim audit and the new-prompt rerun on the D42 and D84 pairs, each trial once (D89, D91) | wrong identified 9 to 2; correct identified 44 to 41 of 48 (outcome level); recall 92% to 85% | development evidence only (prompt tuned on these pairs); needs a fresh unseen set |
| `validation_v1/` | fresh unseen validation of the D89 prompt: 19 trials, 71 outcomes, 204 pairs, rule and lock written first, one run (D92, D93, 2026-11-13) | precision 20/22 (91%), recall 20/25 (80%), 0 bad quotes, 30/71 outcomes unclear; targets met on point estimates only, ranges 72-97% and 61-91% | the first unseen result for the new prompt; AI-drafted labels; a 50-pair blind subset was AI-prepared and reviewed and adopted by Venu (agreement 80%), the other 154 pairs unreviewed; no challenge set |
| `validation_v1/assisted_review/` | assisted review of the 56 pairs the model called unclear in the validation run: frozen list, instructions, report script, AI reviewer's decisions (D96, D103) | by an AI reviewer: 19 settled (4 identified, 15 not identified), 37 stay unclear (generic statement 18, time point 8, different measure 5, subgroup 4, other 2); no time measured | ASSISTED review by the same AI that drafted the labels: not independent, not a human review, not an accuracy test; a person's review is still open |
| `validation_v2/` | new fresh validation, prepared 2026-11-16: 25 trials, 109 outcomes, 325 pairs; human labels only (no AI draft); not labelled yet, no model run (D105) | status: waiting for labels; see `LABELLING.md` |
| `challenge_v1/` | enriched challenge set for four failure modes: 10 trials, 43 outcomes, 151 pairs, one run (D98, D100, D101) | identified 11, 9 right (82%, range 52-95%); 0 wrong identified on wrong-trial, time-point or bare-statement pairs, 2 on subgroup pairs (one paper); 19/43 outcomes unclear | scored on its own, never pooled with validation_v1; tests specific weaknesses, cannot show general accuracy; AI-drafted labels, 50-pair Venu-reviewed subset (original agreement 46/50), reconciliation by Claude alone |
| `rubric.md` | labelling rules and the multi-paper addendum | | current |

## Safety arm (parked since 2026-11-13, D86)

| Folder | What | Result | Use now |
|---|---|---|---|
| `safety/` | first frozen run, 53 trials, plus 10 constructed challenge abstracts (D56) | recall 77%, precision 85%, category 47%; challenge 17/18 | history |
| `safety_v2/`, `safety_v3/` | independent holdouts | v2 recall 92%, precision 66%; v3 recall 93%, precision 91%; no window or numeric case | development data |
| `safety_v4/` | validation with constructed cases (D67) | real recall 87%, precision 98%, all-field 66%; constructed windows 21/21 | development data |
| `safety_v5/` | blind subset plus helper review | recall 83%, precision 76% (D75); four helper proposals marked Wrong by Venu | development data |
| `safety_v6/` | clean run on unseen trials with the locked system (D83) | recall 43/61 (70%), precision 43/50 (86%), windows 0/3, 0 numeric comparisons; targets not met | development data now; see `docs/SAFETY_V6_RESULTS.md` |
| `safety_v3_acquisition/` to `safety_v6_acquisition/` | how trials were drawn (rules, receipts) | | record |
| `development/` | offline replays of saved reader answers with new guards | development evidence only, not model runs on new data | record |

Conventions used from v5 on: `safety_v6/CONVENTIONS.md`. Reference provenance for v6: prepared by a language model, reviewed and adopted by Venu per Venu's statement; not blind-first, not fully independent.

## Where things are
- Run outputs: `runs/` (saved with their receipts).
- Scripts: `run*.py`, `prepare_*.py`, `acquire_*.py`, `audit_*.py`, `label*.py`, `make_*.py` in this folder.
- Decisions and numbers: `docs/DESIGN_DECISIONS.md` (D26 to D86). Current state: `PLAN.md`, "Resume here".
