# TrialTrace: Clinical Trial Evidence Intelligence Platform. Design decisions

*2026-11-17: the project's final title is "TrialTrace: Clinical Trial Evidence Intelligence Platform" (used in the README, docs and dashboard).*

Why the project is built the way it is. One entry per decision: the problem, what we chose,
what it costs, and where to find it. New entries are added after every lesson.

Lesson numbers match the Progress table in `PLAN.md`. Test counts: 756 passing.

**The seven rules behind everything**

1. Unknown is unknown. A failure is never turned into "no".
2. Every claim cites evidence.
3. Differences are "for review", never a verdict. No words like "violation" or "late".
4. Keep the source's precision (`2019-05` stays `2019-05`).
5. Say what was read ("not identified in the abstract", never "not in the paper").
6. Failures are recorded, never dropped.
7. Measure before adding AI.

---

## A. Trust and honesty

### D1. Unknown is never "no"
- **Problem:** a failed download looks the same as "no papers" or "no results".
- **Choice:** four search answers (`ok`, `not_found`, `unavailable`, `invalid`). Empty fields are `None`. "Section missing", "section empty" and "never read" are three different stored states.
- **Where:** `sources/pubmed.py`, `outcomes_status` and `results_status` columns, `paper_searches` table.
- **Proved by:** `test_pubmed_search.py`, `test_repository.py` ("never extracted is unknown, not empty").

### D2. No evidence, no claim
- **Problem:** a report line nobody can check is worthless.
- **Choice:** a `Finding` is refused unless it lists evidence IDs (or is `unknown`). Each evidence row holds the exact value, its JSON path and the hash of the saved file.
- **Where:** `contracts.py` (`Finding`, `Evidence`).
- **Proved by:** `test_contracts.py`.

### D3. Differences are "for review", in neutral words
- **Problem:** a registry/paper difference is not misconduct, and loaded words hurt real investigators.
- **Choice:** neutral category names ("registered, not found in posted results"). A test scans every claim for loaded words.
- **Where:** `compare/*.py`, `test_reported_outcomes.py` (`test_no_loaded_words_in_any_claim`).

### D4. Failures are recorded, not dropped
- **Problem:** a silent failure looks like success.
- **Choice:** registry failures go to `ingest_failures` (history, never deleted). Paper searches go to `paper_searches`. Both say what failed and how many tries.
- **Where:** `ingest_registry.py`, `ingest_papers.py`.

---

## B. Storing data

### D5. Save the raw file first, name it by its content hash
- **Problem:** a parser bug must never lose the original data.
- **Choice:** the untouched response is saved before parsing. The file name is its hash, so the same download is stored once and never overwritten.
- **Where:** `sources/raw_store.py`. **Proved by:** `test_raw_store.py`.

### D6. Snapshots plus observations (A → B → A)
- **Problem:** if a record changes and later changes back, "newest row" shows the wrong version.
- **Choice:** a changed record is a new snapshot. Every look is an insert-only observation. "Latest" follows the newest observation.
- **Where:** migration `003`, `save_snapshot`. **Proved by:** `test_a_b_a_keeps_all_three_sightings_in_order`.

### D7. Our own snapshots are the only history
- **Problem:** the official API serves only the newest record (checked 2026-10-29). The history page is unofficial.
- **Choice:** question 5a compares our own saved versions. With one version the answer is "earlier version not available", never "no change".
- **Cost:** history starts the day we first fetch a trial.

### D8. Known limit: a "new version" can be noise
- **Found on real data:** NCT04644237 came back as a new version. The only differences were a version timestamp and one map coordinate. Question 5a correctly said "no change".
- **Why:** we hash the whole file.
- **Possible fix later:** hash only the sections we use.

### D9. Keep the source's precision
- **Problem:** `2019-05` has no day. Inventing one gives wrong answers.
- **Choice:** dates stay as raw text. The 12-month check returns "not sure" when the month straddles the line. 29 Feb's anniversary is 1 March (written down and tested).
- **Where:** `checks/timeline.py`. **Proved by:** `test_timeline.py`.

### D10. Sponsor names are grouped by a computed key
- **Problem:** variants of one sponsor's name (capitals, punctuation, endings like "Inc." or "S.L.") must count as one sponsor for question 3.
- **Choice:** `normalize_sponsor` makes a key. Real data found three bugs (accents, a trailing "and", dotted endings like `S.L.`). Fixed, plus a `rekey-sponsors` command to recompute stored keys.
- **Where:** `parse/sponsor.py`.

### D11. Tests use their own database
- **Problem:** real data could change a test result, or a test could damage real data.
- **Choice:** tests run on `trialtrace_test`, created and migrated automatically. Every test is rolled back.
- **Where:** `tests/conftest.py`.

### D12. The caller commits
- **Problem:** half-saved trials.
- **Choice:** ingest functions never commit. The CLI commits after each trial. An unexpected error rolls back that trial and the batch continues. If the rollback itself fails, the database is gone and the batch stops.
- **Where:** `cli.py` (`ingest_many`).

### D13. Retry only temporary errors
- **Choice:** timeouts, network errors, 429 and 5xx get up to 3 tries (waits of 1 s, then 2 s). A 404 or a parse error is never retried.
- **Where:** `ingest_registry.py` (`fetch_with_retries`), `ingest_papers.py` (`call_with_retries`).

---

## C. Comparing the registry with itself and with results

### D14. Question 5a: same title means same outcome
- **Choice:** titles match ignoring capitals and punctuation. A removed outcome next to an added one in the same role may be one outcome reworded, so it goes to **needs review**, not "changed". Duplicate titles also go to review.
- **Where:** `compare/registry_changes.py`.
- **Extra:** `show_difference` shows which words changed, with no LLM.

### D15. The registry has three outcome lists
- **Problem:** we read primary and secondary only. The spec also has `otherOutcomes`, which was silently ignored.
- **Choice:** role `other` added, with a fixed order: primary, secondary, other.

### D16. Results arm first (from the V2 blueprint)
- **Problem:** reading paper text is error-prone. Trials with posted results have a structured list of reported outcomes.
- **Choice:** compare registered and reported outcomes in plain code. This gives near-exact ground truth and a clean test set.
- **Where:** `parse/results_parser.py`, `compare/reported_outcomes.py` (`check_reported_outcomes` reads the newest saved version from the database).
- **Real result:** the 4 results trials show no differences (66 outcomes). Every evidence ID a finding cites exists in the `evidence` table.

### D17. What the results comparison does not do
- Descriptions are not compared. Results descriptions are usually longer, so it would only add noise.
- The registered list comes from the same saved version. It may have been edited after results were posted, so this is **not** a comparison with the original plan. Every finding says so.
- `post_hoc` outcomes are "reported, not in the registered list". An unreadable type goes to review. A role is never guessed.

### D18. Findings say how they were made
- **Choice:** `comparison_arm` (results submission, full text or abstract) and `match_method` (deterministic or semantic) on every finding. Both optional.
- **Why:** evaluation can then report precision per kind of evidence. Without them a clean structured match and a guessed one look the same.

---

### D19a. Capture dates are shown in UTC
- **Problem:** the database prints times in its own time zone (yours is Los Angeles). A file saved at 00:30 UTC on 2 Nov showed as "captured 2026-11-01" in a finding.
- **Found by:** a new test on the results check. The same bug was already in questions 1 and 5a.
- **Choice:** the repository converts capture times to UTC when it reads them.
- **Where:** `db/repository.py` (`as_utc`). **Proved by:** `test_capture_dates_are_utc_whatever_the_database_time_zone`.

## D. Papers

### D19. A PubMed hit is only a candidate
- **Problem:** a paper that mentions an NCT ID may not report the trial's results.
- **Choice:** a link check gives `listed` (paper's ID list names the trial), `text_only` (ID in title or abstract) or `not_confirmed`. A `shared` flag marks papers that list other trials too. All are stored with their reason.
- **Where:** `checks/paper_link.py`.

### D20. Papers have their own tables
- **Problem:** one PubMed file holds up to 20 papers, and a paper can name several trials. That breaks the `evidence` table's one-file-one-trial shape.
- **Choice:** `papers` and `paper_links`, keyed by `(pmid, raw_hash)`. When paper-text comparison starts, paper quotes go in a separate `paper_evidence` table. `findings.evidence_ids` is plain text, so it can cite either kind.

### D21. Record every search, and know "ok" can be partial
- **Choice:** `paper_searches` keeps the exact query, counts and failed batches. The view's `answered_fully` is true only if the search answered and every batch was read.
- **Failed batches:** a 200 answer with fewer papers than asked counts as a failed batch (`missing_papers`). If every batch fails the status is `fetch_failed`, not `partial`.

### D22. Be polite to PubMed
- **Choice:** wait 0.4 s before every request (about 3 per second allowed without a key). Retry 429. The API key is optional, sent but never kept in saved URLs. We skipped getting a key.
- **Real result:** the first full run, 11 trials, had no errors.

### D23. Database errors are not caught per batch
- **Choice:** they propagate and roll back, the same as the registry ingest.
- **Why:** a constraint error is a bug we want to see. A lost connection would fail the next batch anyway.
- **Cost:** the search record is lost on such a rollback.

### D24. Linkage is by NCT ID only
- **Real result:** 5 of 11 trials had a paper naming the ID. The other 6 had none.
- **So:** "not identified" always means "not found among papers that name the trial ID". Never "not published". Other linking methods (citations, title matching) are not planned.

---

## E. Method

### D24a. The code-only baseline for papers
- **Problem:** to say an LLM helps, we need a simple version to compare it with.
- **Choice:** for each registered outcome, look for its name in the abstract. Names come from the brackets ("RFS") and the main phrase of the title, plus abbreviation twins (OS = overall survival). Whole words only; a dash equals a space. The answer is `identified` (with the exact sentence), `not_identified`, `ambiguous` or `no_text`.
- **Honest wording:** "not identified" means "not found in the abstract". A paper with no abstract is `no_text` (unknown).
- **Quotes are checked:** each quote carries its offsets, and `quote_is_in_text` proves it is really there. The LLM's quotes will face the same check.
- **Known weakness on purpose:** it matches names, not meaning, so a reworded outcome is missed. The role cue ("primary end point") is read per sentence and is noisy: on real abstracts several registered secondary outcomes picked up a "primary" cue.
- **Where:** `compare/abstract_baseline.py`. **Proved by:** `test_abstract_baseline.py`.

### D25. No agent, a fixed pipeline
- The V2 blueprint adds a LangGraph agent. We build the plain-code baseline first and add an agent only if measured results justify it. A documented "the simple version matched" is an acceptable result.

### D26. Measure before adding AI
- Thresholds and the primary metric are fixed before any LLM code. The LLM must beat the code-only baseline with zero unsupported claims.
- 15 labelled cases (5 held out) are a first experiment, not broad validation. The held-out result is reported even if bad.

### D26a. How the 15 cases are chosen and labelled
- **Case:** one registered outcome plus one linked paper's abstract. Chosen by a fixed rule (primary outcomes first, then a seeded random pick), never by looking at what any system says.
- **Split by trial:** 10 dev cases (3 trials) and 5 held-out cases (2 trials), so a trial never sits on both sides.
- **Blind labelling:** the labelling tool shows only the outcome and the numbered abstract, never the baseline's answer. Quotes are saved by sentence number, so their position is exact.
- **Frozen text:** the abstract is copied into each case, so cases do not change if the database does.
- **Honest limit:** the labels are single-source: the owner labelled 3 cases and adopted a suggested table for the rest. Each label's `annotator` field says so, and results are reported that way.
- **Where:** `evals/make_cases.py`, `evals/label.py`, `evals/rubric.md`.

### D26b. Score dev first, keep held-out sealed
- **Choice:** `evals/run.py` scores only the dev cases unless `--final` is passed. The held-out cases are used once, at the end, for both systems together.
- **First baseline result (dev, 10 cases):** identification 5 of 7 judged cases, role 0 of 3, no unsupported claims, no false "identified". Misses: a recommended "phase II" dose written with a roman numeral, and RFS found in two sentences with different role cues.
- **Rule fixed:** "at least 2 more than the baseline" would have needed a perfect 7 of 7, so it was changed (before any LLM existed, held-out unseen) to: M1 at least +1 (6 of 7), M2 at least +1 (1 of 3), no unsupported claims, no false "identified". The change is written in the rubric's change log.

### D26c. "Quote is real" is not "quote proves the claim"
- **Problem:** the unsupported-claims metric only checked that a quote sits at its place and contains the claimed name. On real dev cases the baseline passed it with the wrong sentence (c09: a background sentence naming FLT3-ITD, not the sentence with the 50.5% result).
- **Choice:** renamed it "bad quotes" and said what it proves. Added M3b: does the system's quote equal the sentence the labeller picked? Baseline on dev: 0 of 3. A person's read of each identified quote is part of the review-time test. The keep rule is unchanged.
- **Where:** `evals/run.py` (`quote_agrees`), `evals/rubric.md`.

### D26d. One small model wrapper (lesson 9)
- **Problem:** model calls fail in many ways, and each run must record which model and how many tokens.
- **Choice:** `trialtrace/llm.py` is the only file that calls the model (OpenAI for now). It retries temporary errors only, turns every failure into a result, and returns model name, tokens and time. It has no prompts and no knowledge of trials.
- **Why:** tests use a fake client (no key, no cost), a failure counts in M6 instead of crashing, and swapping provider means editing one function.

### D26e. The model gives words, code gives positions (lesson 10)
- **Problem:** models count characters badly, and may invent or slightly change a quote.
- **Choice:** the model returns the quote text only. Code finds it in the abstract (exact match), works out where it sits, and checks it contains the name the model claims. A failed check makes the answer `failed` with a reason; it is never turned into a guess.
- **Why:** bad quotes become impossible by construction (M3 = 0) and are visible as failures (M6) and lower M1 instead. The cost: a quote with one changed character is rejected.

### D26f. First LLM dev runs: not yet good enough (lesson 38)
- **Run 1** (`evals/runs/llm-20261106T175116Z-dev.json`, gpt-4o, temperature 0): M1 5/7, M2 0/3, M3 0, M3b 1/3, M4 0, M6 2. Raw answers showed two prompt faults: the model guessed `primary` for c02 (the abstract never says so), and its `name` for c06 was not inside its own quote, so code rejected it.
- **One prompt change** (name must be copied from the quote; role only if the abstract says it). **Run 2** (`...T175201Z-dev.json`): M1 5/7, M2 1/3, M3 0, M3b 1/3, M4 0, M6 2. c02's role is fixed; c06 still rejected.
- **Against the frozen rule** (M1 6/7 or better, M2 1/3 or better, M3 0, M4 0): M2 now passes, **M1 fails (5/7)**. Code caught an invented name on c05 (`name_not_in_quote`), which is the design working.
- **Run 3** (`...T175928Z-dev.json`) after a code change to the name check: a quote may use an abbreviation if the abstract defines it right after one of the registered outcome's own names (e.g. "relapse-free survival (RFS)"), the abbreviation is a whole word in the quote, and `matched_name` becomes that abbreviation. The check was written as a general rule (4 tests: accepted when defined for this outcome; rejected when never defined, defined for another outcome, or absent from the quote), not a special case for c06. Offline, on runs 1 and 2, only c06 changed. Result: **M1 6/7, M2 2/3, M3 0, M3b 1/3, M4 0, M6 1: the keep rule is met on dev.** But the rule was met after a fix prompted by a dev case, so it is provisional until the held-out run.
- **Honest notes:** the prompt was changed once after seeing run 1, and both runs are saved. With 7 judged cases, one case is 14 points. Held-out cases are untouched.

### D26g. Held-out result: the LLM ties the baseline (lesson 38)
- **Run:** `python -m evals.run --system llm --final` (`evals/runs/llm-20261106T180237Z-final.json`, gpt-4o) and the baseline on the same held-out cases (`baseline-20261106T180238Z-final.json`). The held-out cases are now used; any further tuning needs a new held-out set.
- **Held-out (5 cases, 4 judged):** LLM M1 4/4, M2 2/2, M3 0, M3b 2/2, M4 0, M6 0. **Baseline: exactly the same on all of these.** The LLM kept the dev result (6/7, 2/3) and the keep rule was met on dev, but the held-out set cannot separate the two systems: the baseline was already perfect there.
- **Difference seen (M5, reported only):** on c13 (labelled unclear: the abstract reports only grade 3 or higher TEAEs, a narrower set) the LLM said `identified`; the baseline said `not_identified`. On unclear cases the LLM claims more than it should.
- **Cost:** about 11,000 tokens and 17 s for all 15 cases, cents.
- **What this supports:** the LLM is at least as good as the baseline on 15 cases, and better on dev only after fixes prompted by dev cases. It does not show a clear improvement. Decision pending on whether lesson 11 (the same / different / unclear matcher) is worth building.

### D26h. A second, larger experiment with fresh cases (2026-11-06)
- **Why:** the v1 held-out cases are used up, and 15 cases could not separate the LLM from the baseline.
- **Choice:** v2 has 50 dev + 20 held-out cases from new trials, chosen by a written rule (pool from ClinicalTrials.gov, seeded draw, eligibility, draw order) before anything is labelled. New metric M4b counts `identified` on `unclear` cases. v1 files stay untouched as the record.
- **Where:** `evals/v2/rubric.md`, `evals/make_cases_v2.py`.

### D26i. v2 dev results: the LLM does not beat the baseline (2026-11-06)
- **Freeze step 1** done before any run: cases, 70 labels, pool, scoring rule, baseline (hashes in `evals/v2/rubric.md`). The LLM code is frozen later, before held-out, because it is still tuned on dev.
- **Dev (50 cases, 41 judged, 7 yes), baseline vs LLM (gpt-4o, v1-tuned prompt):** M1 33/41 (80%) vs 32/41 (78%); M2 0/7 vs 4/7; M3 0 vs 0; M4 3 vs 5; M4b 2 vs 2; recall 2/7 vs 4/7; precision 2/7 vs 4/11; F1 29% vs 44%; M6 0 vs 5 (3 `name_not_in_quote`, 2 `quote_not_found`); cost 50 calls, 39,000 tokens, 61 s.
- **Against the written keep rule:** M2 passes, M3 passes, M4b passes. **M1 fails** (needs +5 points, got -2.4) and **M4 fails** (needs 0, got 5; the baseline also has 3).
- **What the errors look like:** 4 of the LLM's 5 false `identified` come from one trial (v06-v09): the abstract reports progression-free survival and the registered outcomes are specific populations; two missed `yes` cases (v41, v42) were rejected by the name check; v01's quote did not match the abstract text exactly.
- **Honest note:** the dev set has only 7 `yes` cases, so recall and role results rest on 7 cases. Labels for v06-v09 may reflect a scope judgement (population) that `unclear` could also cover; labels are frozen.

### D26j. Two dev tuning rounds on v2 (2026-11-06)
- **Round 1 (three changes):** a decision order in the prompt (earlier-study results, hypotheses and planned endpoints are not reports; a related but unmatched result is unclear; identified only on a match), quote written before the name with the name copied from the quote, and quote matching that treats any kind of space as one space (v01 failed because the abstract had a non-breaking space). Code in `abstract_llm.py`, 2 tests. **Dev:** M1 36/41, M2 7/7, M4 5, recall 7/7, precision 7/14.
- **Round 2 (prompt only):** "X is superior" with no numbers is a hypothesis; a trial described as still to be done ("patients will receive") reports no results; a subgroup outcome with only an overall result is unclear. **Dev:** M1 40/41 (98%), M2 6/7, M3 0, M3b 3/7, **M4 1**, M4b 3, M6 0, recall 7/7, precision 7/11 (64%), F1 78%; 50 calls, about 45,000 tokens, 50 s.
- **Against the written keep rule** (set before labelling): M1 +17 points over the baseline passes, M2 passes, M3 passes, M4b passes (3 vs baseline 2 plus 2), **M4 must be 0 and is 1 (v38), so the rule is not met.** The baseline also fails M4 (3).
- **Limits:** tuning stopped after two rounds, as planned, to limit overfitting to 50 dev cases (7 yes). Both rounds were driven by looking at dev errors. v06-v09 labels (`no`: a study-design abstract) were kept. The held-out cases have not been used. The rule was not changed.

### D26k. v2 held-out result: exploratory (2026-11-06)
- **Status:** the dev gate failed (M4 = 1, needs 0), so this held-out run is **exploratory** and does not change the selection verdict. Code, prompt, labels and scoring were frozen with hashes before the run (`evals/v2/rubric.md`). No tuning followed. Outputs: `evals/runs/llm-v2-20261106T193214Z-final.json` and the matching baseline file.
- **Held-out (20 cases, 18 judged, 9 yes), LLM vs baseline:** M1 17/18 (94%) vs 12/18 (67%); M2 8/9 vs 3/9; M3 0 vs 0; M3b 3/9 vs 0/9; M4 0 vs 0; M4b 0 vs 0; M6 0 vs 0; recall 8/9 (89%) vs 3/9 (33%); precision 8/8 vs 3/3 (100%, `identified` on `unclear` counted in the denominator); F1 94% vs 50%. LLM cost: 20 calls, about 16,000 tokens, 22 s.
- **Caveat on labels:** 5 of the 9 held-out `yes` labels (v66-v70) come from one trial and rest on one generic sentence. Without them: LLM 13/13 vs baseline 10/13 on judged cases. Quote agreement (M3b) is low for both because the labelled sentence is often a different sentence than the one reporting the result, and because the splitter breaks at "vs.".
- **Two separate conclusions.** (1) Selection: the LLM did **not** meet the predefined keep rule (dev M4 = 1). (2) Performance evidence: the dev gains persisted on fresh trials: the LLM found 8 of 9 reported outcomes where the baseline found 3 of 9, with no false `identified`. A new experiment with fresh held-out cases, not a change to this rule, is the way to act on it.
- **Care:** neither system's matches are verified findings without human review; the baseline had 3 false `identified` on dev.

### D26l. v3 dev run on provisional labels: exploratory (2026-11-06)
- **What v3 is:** a label-enriched challenge set of 100 cases (80 dev, 20 held out) from 75 new trials (`evals/v3/`). Integrity check passed: quotas, new trials, split isolation, hashes, exact quote offsets. The held-out cases were not scored.
- **Label status:** provisional. GPT-4o proposed the labels (the same model the LLM system uses), then corrections from source text; no human adjudication yet (`review_decisions.jsonl` is blank). The rubric is a draft and not frozen.
- **Dev run (80 cases, 64 judged, 40 yes), baseline vs LLM:** M1 36/64 (56%) vs 56/64 (88%); M2 14/40 vs 35/40; M3 0 vs 0; M3b 1/40 vs 16/40; M4 3 vs 3; M4b 5 vs 10; M6 0 vs 0; recall 38% vs 88%; precision 65% vs 73%; F1 48% vs 80%; LLM cost 80 calls, about 77,000 tokens, 99 s. No tuning was done.
- **Against the proposed v3 rule (not frozen):** M1 passes by a wide margin, M2 and M3 pass, but **M4 = 3 (needs 0) and M4b 10 (baseline 5 + 2 = 7) would fail.**
- **Caveats:** the labeller and the evaluated system are from the same model family, so agreement may be inflated (M3b rose from 3/7 on v2 to 16/40). Treat these numbers as exploratory until the labels are human-adjudicated.

### D26m. v3 dev on reviewed labels (2026-11-06)
- **Labels:** 80 dev decisions drafted by claude, reviewed and adopted by Venu (`review_decisions.jsonl`, `drafted_by` recorded; loader `evals/apply_review_v3.py`). 6 of 80 changed from the GPT-4o suggestions (no to unclear 2, unclear to no 1, unclear to yes 1, yes to unclear 2); among kept yes labels, 2 roles and 1 result sentence changed. Still **model-drafted, owner-adopted, not independent human labels**; the test split has no decisions yet; the rubric is not frozen.
- **Dev (80 cases, 62 judged, 39 yes), baseline vs LLM:** M1 36/62 (58%) vs 55/62 (89%); M2 13/39 vs 34/39; M3 0 vs 0; M3b 1/39 vs 19/39; M4 2 vs 3; M4b 6 vs 12; M6 0 vs 0; recall 38% vs 90%; precision 65% vs 70%; F1 48% vs 79%. LLM: 80 calls, about 77,000 tokens, 98 s. No tuning.
- **Against the proposed v3 rule (not frozen):** M1, M2, M3, M6 pass; **M4 = 3 (needs 0) and M4b 12 (baseline 6 + 2 = 8) fail.** The LLM says `identified` on 12 of the 18 unclear cases: overclaiming on related-but-not-matching results is its main weakness. The baseline fails M4 too (2).

### D26n. Three tuning rounds on v3 dev (2026-11-06)
- **Start (reviewed labels):** M1 55/62, M4 3, **M4b 12**, recall 90%, precision 70%. The errors fell into classes: outcome only listed as an endpoint, protocols/reviews/other studies, a result that is not the registered measure (subset, subscale, different time point, subgroup), results pooled across trials.
- **Round 1 (prompt, by class):** M1 47/62, M4 1, M4b 3, recall 69%, precision 87%: it over-corrected and called real results `unclear`.
- **Round 2 (prompt):** `unclear` only when a specific mismatch can be named; different wording or fewer details is not a mismatch; extra `mismatch` field. M1 52/62, M2 32/39, M4 3, M4b 6, M6 1, recall 85%, precision 79%.
- **Round 3 (code):** the model also says what kind of paper it is (`document`); code turns an `identified` from a protocol or review into `not_identified` with a reason (`reason` field). 6 new tests. **Final dev: M1 52/62 (84%), M2 30/39, M3 0, M3b 20/39, M4 1, M4b 8, M6 0, recall 79% (31/39), precision 78%, F1 78%.** Baseline on the same labels: M1 58%, M2 13/39, M4 2, M4b 6, recall 38%, precision 65%, F1 48%.
- **Against the proposed v3 rule (not frozen):** M1 (+26 points), M2, M3, M4b (8, at the limit of baseline 6 + 2) and M6 pass; **M4 = 1 (needs 0): b022** (an acute crossover result for a 24-week outcome). Tradeoff: overclaims fell from 12 to 8 and false identified from 3 to 1, at the cost of recall (90% to 79%).
- **Run-to-run noise** (same prompt, 4 runs): M1 52 to 53 of 62, M2 30 to 31, recall 31 to 32 of 39, M4 1, M4b 8, M6 0. So about plus or minus 1 case.
- **Round 4 (tried, reverted):** "different wording is never a mismatch" with generic examples, the trial ID in the prompt and a pooled-trials hint. Two runs: M1 52 and 53, M2 31 and 32, recall 82% and 85%, M4b 9 and 9: no gain beyond noise, so it was reverted. **Prompt tuning has plateaued;** the remaining misses are mostly different wording the model will not equate, pooled or subgroup results, and a few label judgement calls. Further gains would need a stronger model or a second check, not more prompt wording.
- **Limits:** three rounds tuned on the same 80 dev cases; each change was driven by looking at dev errors, so dev scores are optimistic. LLM output varies a little between runs even at temperature 0 (not measured). The test split has no reviewed labels yet and has not been scored. Labels are model-drafted, owner-adopted.

### D26o. Model comparison on v3 dev (2026-11-06)
- **Why:** prompt tuning had plateaued (D26n); the remaining lever was the model. The same frozen prompt, the same reviewed v3 dev labels, dev only, `temperature` left out (these models reject 0; `llm_temperature=None`, `--model`, `--temperature none`). Outputs in `evals/runs/llm-<model>-v3-*`, text summaries in `evals/v3/bakeoff/`.
- **Results (62 judged, 39 yes):**

| | gpt-4o | gpt-5.5 | gpt-6.1-sol | gpt-6-sol | gpt-6.1-terra |
|---|---|---|---|---|---|
| M1 | 53 | 53 | 53 | 52 | 50 |
| M2 | 31 | 30 | 30 | 29 | 27 |
| M4 (false identified) | 1 | 0 | 0 | 0 | 0 |
| M4b (overclaim on unclear) | 8 | 2 | 3 | 2 | 2 |
| Recall | 82% | 85% | 82% | 79% | 74% |
| Precision | 78% | 94% | 91% | 94% | 94% |
| F1 | 80% | 89% | 86% | 86% | 83% |
| Output tokens (80 calls) | 6k | 39k | 8k | 16k | 12k |
| Time (80 calls) | 105 s | 538 s | 217 s | 178 s | 237 s |

- **Repeat:** `gpt-6.1-sol` run twice gave identical counts (M1 53, M2 30, M4 0, M4b 3, recall 32, precision 91%).
- **What it shows:** all four newer models stop the false and overclaimed `identified` answers (M4 = 0, M4b 2 to 3 instead of 8) with the same recall and M1 as `gpt-4o` or a little lower. On the proposed v3 rule (not frozen) every one of them passes all conditions, including M4 = 0 and M6 = 0, which `gpt-4o` could not. `gpt-5.5` has the best F1 but about 5 times the output tokens and time of `gpt-6.1-sol`.
- **Limits:** one run per model except `gpt-6.1-sol`; dev only, labels model-drafted and owner-adopted; the prompt was tuned on `gpt-4o`; the model choice was made after looking at dev results, so it is a dev-tuned choice. Held-out has not been used.

### D26p. v3 held-out result: exploratory (2026-11-06)
- **Status: exploratory.** The 20 test labels are provisional (GPT-4o suggestions plus source-text corrections, no human review); dev labels are model-drafted and owner-adopted; the v3 keep rule was proposed, never formally accepted. Everything was frozen by hash before the run (`evals/v3/freeze_exploratory.md`); one run per system; no change afterwards. Outputs: `evals/v3/final/`, `evals/runs/*-v3-*-final.json`. **The test split is now used.**
- **Held-out (20 cases, 16 judged, 10 yes):**

| | baseline | gpt-6.1-sol | gpt-5.5 |
|---|---|---|---|
| M1 | 13/16 (81%) | 14/16 (88%) | 14/16 (88%) |
| M2 | 6/10 | 8/10 | 8/10 |
| M3 | 0 | 0 | 0 |
| M3b | 1/10 | 9/10 | 9/10 |
| M4 | 0 | 0 | 0 |
| M4b | 0 | 2 | 1 |
| M6 | 0 | 0 | 0 |
| Recall | 7/10 (70%) | 10/10 | 10/10 |
| Precision | 7/7 (100%) | 10/12 (83%) | 10/11 (91%) |
| F1 | 82% | 91% | 95% |
| Cost (20 calls) | none | 25k tokens, 58 s | 31k tokens, 112 s |

- **Dev re-scored in the same run (third look):** baseline M1 36/62; gpt-6.1-sol 51/62, M4 0, M4b 3, F1 85%; gpt-5.5 51/62, M4 0, M4b 2, F1 88%. Within the earlier noise range.
- **Against the proposed held-out claim rule (not accepted):** M1 higher (+1 case), M2 no worse, M3 0, M4 0 pass; **M4b no worse fails** (LLM 2 and 1 vs baseline 0). So no selection verdict is claimed.
- **Two conclusions.** Selection: none, because the run was exploratory. Performance: the LLM finds all 10 reported outcomes where the baseline finds 7, with no false `identified`, but it overclaims on 1 to 2 of 4 unclear cases where the baseline overclaims on none.
- **Caveats:** only 16 judged cases, so one case is 6 points, and M1 differs by one case. The baseline does far better on held-out (81% M1, 70% recall) than on dev (58%, 38%): the test split is easier, or its labels are friendlier. M3b is 9/10 for the LLM and 1/10 for the baseline partly because the test labels were drafted by a GPT-4o model, so agreement may be inflated. Human review of the test labels is the main missing step.

### D26q. v4: 20 fresh cases for one more final run (2026-11-06)
- **Why:** the v3 test split was used once, on unreviewed labels. v4 gives a new test set for the two chosen models (`gpt-6.1-sol`, `gpt-5.5`) and the baseline, with labels reviewed by a person before any run.
- **Selection (fixed before labelling):** 60 trials drawn with seed 20261109 from the v2 pool, excluding every trial stored or used in v1 to v3 (`evals/v4/draw.txt`, ingested); eligible = a listed paper with an abstract and at least 5 registered outcomes; the first 4 eligible in draw order (NCT00121472, NCT01430403, NCT02092220, NCT01155466); per trial the 5 outcomes by the v2 rule (primary first, then seeded random). `evals/make_cases_v4.py`.
- **Labels:** drafted by claude (a different model family from the evaluated GPT models), 6 yes / 6 unclear / 8 no, 11 rows flagged medium confidence, with a reason per row (`evals/v4/draft_labels.md`). To be reviewed and signed by the human reviewer; the loader refuses unsigned rows (`evals/apply_review_v3.py --cases ... --out ...`). No system has been run on v4.
- **Limits:** 4 trials means the 20 cases are correlated (5 per trial); the abstract-only limits and the model-drafted label caveat still apply.

### D26r. v4 final run: fresh cases, human-reviewed labels (2026-11-06)
- **Set-up:** 20 fresh cases from 4 trials (D26q), labels drafted by claude and reviewed and adopted by Venu (t05 changed to `no`; t16 sentence 10 from the registry record). Frozen by hash before any run (`evals/v4/freeze.md`). One run per system, no change afterwards. Outputs in `evals/v4/final/` and `evals/runs/*-v4-*-final.json`. No selection verdict (20 cases from 4 trials).
- **Result (15 judged, 6 yes, 5 unclear, 9 no):**

| | baseline | gpt-6.1-sol | gpt-5.5 |
|---|---|---|---|
| M1 | 10/15 (67%) | 13/15 (87%) | 14/15 (93%) |
| M2 | 1/6 | 6/6 | 6/6 |
| M3 | 0 | 0 | 0 |
| M3b | 1/6 | 6/6 | 5/6 |
| M4 | 0 | 0 | 0 |
| M4b | 0 | 1 | 2 |
| M6 | 0 | 0 | 0 |
| Recall | 1/6 (17%) | 6/6 | 6/6 |
| Precision | 1/1 | 6/7 (86%) | 6/8 (75%) |
| F1 | 29% | 92% | 86% |
| Cost (20 calls) | none | 26k tokens, 51 s | 33k tokens, 115 s |

- **Errors:** both models said `ambiguous` on t05 (label `no`), which counts as wrong for M1; gpt-6.1-sol also said `ambiguous` on t15 (label `no`). Overclaims on unclear: t07 (both models) and t13 (gpt-5.5). The baseline found only 1 of 6 reported outcomes (t01); it missed the rest because the abstract words them differently from the registry.
- **Pattern across v2, v3 and v4:** the LLM finds most reported outcomes where the baseline finds few, with no false `identified` from the newer models; its recurring weakness is overclaiming on a few `unclear` cases; the baseline never overclaims but misses most positives.
- **Caveats:** 15 judged cases from 4 trials (one case is 7 points; cases within a trial are correlated); one run per model; the abstract-only limit; the labels are model-drafted and reviewer-adopted.

### D28. The paper reader in the product: frozen config, two stored predictions (2026-11-06)
- **Problem:** after the evaluations we need the LLM inside the pipeline without trusting it alone, and every stored answer must say which configuration produced it.
- **Choice:** `gpt-6.1-sol` is the provisional primary reader (temperature not sent; `gpt-5.5` comparison only), frozen in `paper_reader.py` with a prompt fingerprint and a schema version. `match-papers` runs the LLM and the baseline on every registered outcome against up to 10 listed papers per trial (lowest PMIDs first; the limit is rarely reached), stores two separate insert-only rows per pair (`paper_matches`, one prediction per system, outcome, paper and run), and ranks pairs for review: disagreements, unclear answers and failures are high priority.
- **Rules:** a failed or code-rejected LLM answer keeps its error code and the raw answer; the baseline is then shown only as a provisional fallback; an abstract-only `not_identified` is never a finding of omission; papers beyond the limit are recorded as not read (PMID and title, `paper_match_skipped`) and named in the limitations of that trial's findings, so "not identified" is never read as "not in any paper".
- **Not yet:** these predictions are not yet reviewable findings (that needs evidence rows for abstract quotes: Phase 2); no dashboard; no safety (Q6) yet.

### D30. Paper matches become reviewable findings (2026-11-08)
- **Problem:** the LLM and baseline answers sat in `paper_matches` where neither the report nor the review queue could see them, and a finding cannot make a claim without evidence.
- **Choice:** `compare/paper_outcomes.py` builds one `Finding` per registered outcome per paper from the newest stored match run. It cites the registered outcome field and the abstract text each system used; those quotes are saved as `pubmed` evidence rows when the match run is stored (`evidence_id` = `<NCT>:<paper hash 12>:abstract[start:end]`, and one row for the whole abstract). Categories: both found (paper_reports_outcome), neither found (paper_outcome_not_identified), systems disagree, model unclear, check failed, and one `unknown` finding for a trial never matched.
- **Rules kept:** neutral wording with no verdict words; "not identified in the available text (abstract)", never an omission; every finding says it is provisional; the model, prompt fingerprint and code checks are named in the limitations; disagreements, unclear answers and failures are high priority in the queue, agreement appears only with `--all`.
- **Not yet:** a reviewer decision of "unclear", review ordered by risk on screen, and a stored link from a reviewer correction to the model's original prediction (Phase 2b).

### D31. Two evaluation levels for several papers per trial (2026-11-08)
- **Problem:** the product reads up to 3 papers per trial, but every evaluation so far used one paper per trial, so "is the outcome reported in at least one of the papers" was never measured.
- **Choice:** evaluate each outcome-paper pair on its own (Level A, primary), then roll up per outcome (Level B, secondary) with a three-valued rule: any `identified`; else any `unclear`; else any failed or unread paper is `unknown`; else `not identified in the checked abstracts`. Report micro-averages and per-trial averages, and keep leave-one-trial-out.
- **Amended 2026-11-08 (headline):** both levels are reported together, and the headline is the outcome-level, evidence-supported score: a true positive needs the system to say identified, a cited paper labelled `yes` for that outcome, and a quote that passes the code check. Plain any-paper accuracy can look correct while pointing at the wrong paper (a false positive in one paper masking a miss in another), so it is shown beside the headline, not as the headline. Pair-level metrics remain the diagnostic. Targets are set after the dev distribution is known and before the frozen test run, with confidence intervals and per-trial results. Outcome-level truth needs every paper labelled for every outcome, so it is measured on the Phase 5 set, not on v1 to v4.
- **Why:** pair-level scores show how well the identification works; the roll-up shows how much coverage the chosen papers give. The any-paper rule gives a system several chances to overclaim, so the roll-up must not replace the pair-level metrics. Level B accuracy needs a labelled multi-paper set (Phase 5).

### D32. Reuse a stored LLM answer instead of paying for the same call (2026-11-08)
- **Problem:** every `match-papers` run called the model again for every outcome-paper pair, even when the same outcome and abstract had been answered before. That wastes money and time, and gives a different answer from the same question (the model is not deterministic), which makes findings flip between runs.
- **Choice:** each LLM row stores `input_hash`, a fingerprint of everything the model saw (outcome text, time frame, abstract, model, prompt fingerprint, schema version, temperature setting). A new run looks the fingerprint up first and copies a usable stored answer (identified, not_identified, ambiguous) forward, with `reused_from` pointing to the original real call, the raw answer kept, and no tokens or seconds counted again. Failures are retried. `--refresh` forces a fresh call. The baseline is free and always recomputed. The run summary reports `model-calls` and `reused`.
- **Why it is safe:** changing the model, the prompt or the temperature setting, or a different abstract or outcome text, changes the fingerprint, so nothing stale is reused. A registry edit that leaves the outcome text alone keeps the answer (the outcome is the same question). Rows from before this change have no fingerprint and are never reused.
- **Checked on real data:** NCT04644237, 26 pairs: the first run made 26 model calls, the second made 0 and reused 26, with the same 6 high-priority findings.

### D33. Say when the stored papers moved on after the match run (2026-11-08)
- **Problem:** a report shows each trial's newest match run. If a later PubMed search stored a new paper, or a paper's abstract text changed, the findings still looked complete. Reuse of stored answers (D32) is correct for the same input, but it cannot know that the inputs grew.
- **Choice:** `check_paper_outcomes` compares the stored listed papers with the match run. A listed paper with an abstract that the run neither read nor recorded as skipped by the paper limit is named by PMID in every finding's limitations ("stored after this match run were not read"). A paper the run read whose abstract text is different in a newer download is named too. A new download with the same abstract text is not flagged (a metadata change is not a new question). Running `match-papers` again clears the notes. The registry side already had the equivalent note (outcome from an older saved version).
- **Not automatic by design:** nothing re-ingests or re-matches by itself. The dashboard's "Analyze Trial" (Phase 3) will run ingest, PubMed search, matching with reuse, and the report in order, skipping steps whose inputs did not change.

### D34. Reuse only answers from the model version being served now (2026-11-08)
- **Problem:** the reuse fingerprint (D32) holds the configured model name. A provider can move an alias to a newer version, and an old answer from the previous version would be reused as if it came from the current one. The served version is only known from a response, so it cannot be part of the fingerprint.
- **Choice:** every real LLM call stores `served_model`, the name the API reports. Each `match-papers` run first makes one probe call ("Reply with the single word OK", about 20 tokens) to read the served name, and reuses a stored answer only if its `served_model` equals it. A reused row copies the original's `served_model`. If the probe fails, reuse falls back to the fingerprint alone and a warning is printed. Rows from before the column existed have no `served_model` and are not reused once the check is on (one paid refresh).
- **Limit:** the API reports the alias itself for some models (`gpt-6.1-sol` reports `gpt-6.1-sol`), so for those the check can only notice a change if the provider changes the reported name. A dated model name (such as `gpt-5.5-2026-04-23`) gives full protection. The probe is repeated every run because it is the only way to see a change even when every answer would otherwise be reused.
- **Checked on real data:** NCT04644237, 26 pairs: the first run after the change paid for 26 calls (no stored served model), the second made 0 and reused 26.

### D35. Review of paper findings: unclear, priority order, evidence text, audit link (2026-11-08)
- **Problem:** a reviewer could only approve, edit or reject. "I cannot tell from this abstract" is an honest answer that was forced into approve or reject. The queue was ordered by trial, not by risk, so a disagreement could sit behind plain facts. The reviewer saw evidence IDs, not the quote. And nothing tied a finding to the match run that produced it.
- **Choice:** (1) a fourth decision, `unclear`, with a required reason (checked in `record_review` and by a database constraint, migration 018). (2) `review_priority`: paper findings where the systems disagree, the model could not match, or the model's answer could not be used are high; other differences are normal; plain facts and paper results where both systems agree are low. The queue is sorted by it (stable within a level) and the screen shows the level. (3) the evidence text is loaded in one query and printed under each ID, cut at 300 characters. (4) `findings.source_run_id` records the match run behind a paper finding. Reviews live in their own table and never touch `paper_matches`, so the model's original answers and the reviewer's corrections stay separate and linkable.
- **Not yet:** a stored link relationship per paper that a reviewer confirms (reports results, describes protocol, mentions only, not this trial): Phase 2b part 2.

### D36. Outcome-level roll-up findings, and the summary as the review unit (2026-11-08)
- **Problem:** a trial with several papers produced one finding per outcome per paper (26 for NCT04644237). That is accurate but not what a person reads or should review: the same outcome would be judged once per paper.
- **Choice:** `check_paper_summary` makes one finding per registered outcome from the newest match run, by the D31 rule applied to each system separately: any paper identified, else any unclear, else any failed or unread paper is unknown, else not identified in the checked abstracts. The model is the primary reader; the baseline is rolled up the same way and a difference is marked `disputed` (categories `paper_summary_reported`, `..._reported_disputed`, `paper_summary_unclear`, `paper_summary_not_identified`, `..._not_identified_disputed`, or `unknown`). Papers left out by the limit, stored after the run, or with changed abstracts count as unread, so they turn "not identified" into "unknown" and are named. An identified outcome cites the registered outcome and the supporting quotes only, and says that code verified the quote but a person must confirm that the sentence reports the result.
- **Review:** the summaries are the default queue (disputed and unclear are high priority, agreement is low and only with `--all`). The per-paper findings (`paper_outcome:...`) stay in the report and database as the detail, shown with `--all`.
- **Checked on real data:** NCT04644237: 13 outcome findings from 26 pairs (2 reported, 1 reported but disputed, 3 unclear, 7 not identified, counted over the corpus report).
- **Not yet:** a measurement of outcome-level accuracy against labels. That needs the multi-paper labelled set (Phase 5, D31).

### D37. A person confirms how each paper relates to the trial (2026-11-09)
- **Problem:** the code link check (listed / text_only / not_confirmed) only says the trial's ID appears with the paper. It cannot say whether the paper reports the results, describes the protocol, only mentions the trial, or is about another trial.
- **Choice:** a separate answer from a person per (trial, paper file): `reports_results`, `describes_protocol`, `mentions_only`, `not_this_trial`. Table `paper_link_reviews` (migration 019), insert-only like `reviews`; the newest answer is current (`current_paper_link_reviews`). `not_this_trial` needs a reason (checked in code and by the database). The code's verdict in `paper_links` is never changed; the person's answer sits beside it. CLI: `python -m trialtrace review-links --reviewer NAME [NCT...]` asks only about papers with no answer yet.
- **Not yet:** nothing reads these answers. Using them (for example, leaving `not_this_trial` papers out of the roll-up, or labelling the Phase 5 set) is a later step, so no result changes today.

### D38. Roll-up review fixes: papers differ, completeness, claim wording (2026-11-09)
- **From an outside read of `paper_rollup.py`.** Four observations; three changed the code, one is a dashboard rule.
- **Papers differ:** both systems could say `identified` while citing different papers, and the outcome-level answer then looked like agreement (low priority). Now, if the two systems' identified papers share no paper, the category is `paper_summary_reported_papers_differ`: high risk, in the default queue, and the limitations name both paper sets. Sharing at least one paper is agreement (the baseline finding extra papers is not flagged, it would be noise).
- **Evidence completeness:** a separate field on the finding (`complete` / `incomplete`, migration 020), not a change to the roll-up rule. Incomplete = some listed papers unread (limit, new, changed) or a failed paper. An agreed `paper_summary_reported` that is incomplete is normal priority, not low, and is in the default queue; its limitations say more papers could report the outcome. "Not identified" with unread papers was already `unknown`.
- **Claim wording:** "The model identified evidence that may report a result for..." (a verified quote is not proof the sentence reports the result; a person confirms).
- **Dashboard rule (Phase 3, no code yet):** `disputed` means the model and the baseline differ. It never means the paper contradicts the registry. The two must be labelled separately in the dashboard and in evaluation. This paper arm finds whether an outcome is reported, not registry-vs-paper discrepancies.

### D39. Pilot evaluation of the trial-with-several-papers roll-up: NCT04644237 (2026-11-09)
- **Set-up:** 13 registered outcomes x the 2 papers the match run read = 26 pairs (`evals/multipaper/NCT04644237/`). Labelled blind by the project owner (single annotator; no system answer shown). Scored from the STORED answers in `paper_matches` (no new model calls) by `evals/run_multipaper.py`: Level A per pair, Level B per outcome by the roll-up rule, headline = evidence-supported precision and recall (the system says identified, a paper it identified is labelled yes, its quote passes the code check). Label and case file hashes are saved with the result (`evals/runs/multipaper-*.json`). Before scoring, one label rule was applied consistently: m07 (any treatment-emergent adverse event) reported only as grade >= 3 drug-related events is `unclear`, as in the c13 precedent.
- **Result (13 outcomes, 2 truth-yes):** both systems found both reported outcomes (recall 2/2) and both called one more outcome identified (precision 2/3). Pairs: baseline 21/23 on yes/no pairs, model 20/23. The baseline falsely identified the investigator-assessed response rate in both papers (it matched the blinded-review response rate, which is a different outcome); the model did not, but marked 3 `no` pairs unclear. The model's third identified call was a quality-of-life outcome labelled `unclear` (the abstract says quality of life was preserved with no instrument or change values). The model's quotes were the labelled sentence in 3/3 yes pairs, the baseline's in 1/3.
- **Extended in D40 to 7 trials.** Limits of this first pilot: one trial, 2 truth-yes outcomes: the numbers say the roll-up and the scorer work, not how accurate the system is. A validation needs several trials with several labelled papers each (Phase 5). The abstracts only; "no" means not in the abstract.

### D40. Multi-paper pilot on 7 trials (2026-11-10)
- **Set-up:** the D39 trial plus 6 fresh trials chosen by a rule written before labelling (not in any v1-v4 case file; at least 2 listed papers with abstracts; at most 14 outcomes and 5 papers; the smallest labelling load, not a random draw). 104 pairs, 40 outcomes, labelled blind by the owner; labelling rules in the rubric addendum. Answers are the stored ones (the first trial from match run 20261108, the six others from 20261109, `gpt-6.1-sol`, one run, no tuning on these cases). `python -m evals.run_multipaper --all` adds the counts of every fully labelled trial (micro-average) and saves the per-trial results with the label hashes.
- **Result:** labels: 7 yes, 77 no, 20 unclear pairs; outcome truth: 6 yes outcomes. Outcome-level, evidence-supported: model recall 6/6, precision 6/10; baseline recall 3/6, precision 3/8. Pair-level yes/no accuracy: baseline 79/84, model 75/84. No bad quotes and no failures for either system. The model's quote is the labelled sentence in 6/7 yes pairs, the baseline's in 1/4. The baseline falsely identified 2 `no` pairs (the investigator-assessed response rate matched the blinded one); the model identified none. The model marked 9 `no` pairs unclear (a review cost, not a false claim). Both systems called some `unclear` outcomes identified (model 4, baseline 4 of 20 unclear outcome-level cases), which is where the labelling rule about qualitative and pooled results decides the precision.
- **Limits:** 6 yes outcomes from 7 small trials, correlated within trial; 95% Wilson intervals are wide (model recall 6/6: 61% to 100%; baseline recall 3/6: 19% to 81%; model precision 6/10: 31% to 83%). One annotator. The precision depends on one labelling rule (qualitative statements are `unclear`). These cases were chosen after the model had already been tuned on other sets, so they are fresh, but the sample is too small for a selection verdict. Abstracts only.

### D41. Agreed "reported" findings with no result in the sentence go to review (2026-11-10)
- **Problem (from D40):** the model called 4 outcomes identified that the labels say are `unclear` (a sentence that names the outcome but gives no result). If the baseline also said identified, the finding was `paper_summary_reported`: low priority, not in the default queue.
- **Rule:** `has_result_detail` (`compare/paper_rollup.py`): the supporting sentence needs a number, a test or the word "significant" next to the outcome's name: after the name, up to the first comma or semicolon outside brackets (numbers in brackets right after the name count); a time point such as "at 3 or 6 months" is not a result. If an agreed `paper_summary_reported` has no quote with that, the category becomes `paper_summary_reported_no_detail`: high risk, in the default queue, with a limitation line saying why. It is a heuristic and can only add review, never remove it.
- **Measured routing** (new line in `run_multipaper.py`, uses the real roll-up): over the 7 labelled trials, of the model's 4 overclaims (identified, truth not yes), 4 now go to review and 0 are missed; with the rule off, 3 were queued and 1 was missed. The correct identified outcomes sent to review stayed 3 of 6 (the rule added none on these data).
- **Limits:** the rule was designed after reading these 11 supporting sentences, so this result is in-sample and says nothing about unseen cases. Known false flags: a zero-event result such as "no social harms occurred" and a sentence where the number comes after a comma ("feasible and attendance was excellent, with 29 (100%)...") are flagged although the label is yes. That costs one review, never a wrong claim. It does not read meaning: a number next to the name proves nothing about whether the sentence reports the registered result.

### D42. Final multi-paper evaluation: 19 trials, 204 pairs (2026-11-10)
- **Set-up:** trials drawn by a written rule (`evals/final/RULE.md`, seed 20261110, 6000 registry studies read, never used in any earlier case file), ingested, the first 19 eligible taken (>= 2 listed papers with abstracts, 2 to 7 outcomes). All 204 labels written by Claude from the abstracts only, blind to system answers, before `match-papers` ran; hashes in `evals/final/freeze.md`. One run (`gpt-6.1-sol`, frozen reader, routing rule D41 already in place), no change afterwards. Labels: 29 yes, 148 no, 27 unclear pairs; 26 of 72 outcomes have truth yes.
- **Result (outcome level, evidence-supported):** model recall 22/26 (85%, 95% CI 66-94%), precision 22/26 (85%, 66-94%); baseline recall 8/26 (31%, 17-50%), precision 8/12 (67%, 39-86%). Pairs: model 161/177 (91%), baseline 150/177 (85%) on yes/no pairs. Model quote = labelled sentence 20/24, baseline 3/9. No bad quotes, no failures. The 4 model overclaims all went to review (0 missed; out-of-sample for the D41 rule). Cost: 17 of the 22 correct identified outcomes also went to review (the baseline cannot confirm most of them).
- **Limits:** labels are model-drafted (Claude), not human-verified; the evaluated model is a different family, but shared blind spots and label errors are possible. A random human spot-check is still owed. One labeller, one rule set (rubric addendum). The pool is the first 6000 API results, not the whole registry. Abstracts only. 19 trials, correlated pairs within a trial.

### D29. Safety (Q6) follows the same strategy (2026-11-06)
- Registry adverse events parsed by code into a `SafetyEvent` contract; a code baseline and an LLM reader for the paper side; comparison rules that answer "not enough detail to compare" when a needed field (denominator, threshold, time window, grade) is missing; its own rubric, fresh cases with provenance, frozen config, one run. Adverse-event outcomes were the hardest cases in the outcome evaluation (all events vs grade 3 or higher, per patient-year vs per infusion), so the baseline-versus-LLM comparison and human review matter more here.

### D27. LLMs read, code decides
- The LLM only extracts fields with quotes and answers small "same / different / unclear" questions. Code checks every quote appears in the text and applies the comparison rules.

---

## Known limits (honest list)

- The LLM check proves a quote is real and names the outcome, not that the sentence reports its result (M3 wording, M3b, M8 human read). If a sentence appears twice in an abstract, its position is the first occurrence (none of the 15 eval abstracts repeats a sentence).

- The model wrapper exists but no prompt or real model call yet; no LLM evaluation yet.
- Papers are matched by NCT ID only; 6 of 11 trials had none.
- 4 of 11 trials have posted results, and all 66 outcomes matched, so real data shows no differences yet.
- History starts when we first fetch a trial, and a whole-file hash can call noise a "new version" (D8).
- The corpus is 11 trials. Counts are "in the ingested corpus only".

### D42 update: human spot-check of the labels (2026-11-10)
A person labelled 30 pairs blind (10 yes / 10 unclear / 10 no by my draft). Agreement 27/30 (90%), kappa 0.85; all 10 yes matched incl. roles. The 3 misses were all on the unclear/no line (my own inconsistency); the human labels were adopted and the scoring re-run on stored answers (no new API calls). Final numbers (19 trials, 72 outcomes): model pairs 162/178 (91%); outcome headline precision 22/26 (85%, CI 66-94), recall 22/26 (85%); baseline recall 8/26 (31%), precision 8/12; quote is the labelled sentence 20/24 vs 3/9; 4/4 overclaims routed to review, 0 missed; 17/22 correct ones also reviewed. Limit: 30 of 204 pairs human-checked, not all; abstracts only; pool = first 6000 API results.

### D42 update 2: human check of "not identified" (2026-11-10)
A person labelled blind 20 of the 34 outcomes the model called "not identified" (these are not sent to review): 0 had a reported result (19 no, 1 unclear). One draft label was corrected (qualitative "no major side effects", no -> unclear); outcome headline numbers unchanged (22/26, 85%), pair accuracy 161/177. Reading: no sign of silent misses in this sample (0 of 20; upper 95% bound about 16%), but the sample is small. Decision: keep "not identified" out of the review queue, and re-check on a larger sample before any stronger claim.

### D43. Safety S1: what the registry adverse-event data really looks like (2026-11-10)
Read all 258 stored trials with an adverse-event module (and 10 in detail). Findings that shape the parser and rules:
- **Death counts are often missing:** only 123 of 258 trials give deaths per arm. Missing = unknown, never 0. Deaths may also appear only as an event term.
- **Arms:** 45 single-arm, 144 two-arm, 69 with 3+ arms (up to 6+). Some have an extra pooled group ("All Stribild", 3 trials show total-like titles): never add arms up, and do not match a paper's number to a pooled group by accident.
- **Counts oddities:** 10 trials have at-risk = 0 (one is an "Observational Study Section" group); 2 trials lack at-risk counts; 116 events lack `numAffected`; `numEvents` is missing in most event stats. So affected / at-risk is the main number; `numEvents` is optional.
- **Duplicate terms:** the same term + organ system can appear twice in one list (5 trials other, 1 serious), each with different counts. Keep both rows; do not merge or overwrite.
- **Thresholds:** `frequencyThreshold` is 0 in 114 trials (all events listed), 5 in 113, 1-4 in the rest. Non-serious events are only complete when the threshold is 0.
- **Event vocabulary is free text:** about 90 spellings (MedDRA with many version formats, CTCAE, COSTART, none in 77 trials). Store as given; compare terms by words, never by vocabulary version.
- **Assessment type:** `NON_SYSTEMATIC_ASSESSMENT` events are free-text, sometimes a sentence ("Chest pain and slightly elevated Troponin I"), not a clean term.
- **Time frame:** present in 191 of 258; often long text and sometimes different for serious vs non-serious events.
- **Consequence:** the parser must keep every number nullable, keep duplicate rows, store the threshold and assessment type, and the comparison must refuse numbers without a clear arm and denominator.

### D44. Safety S2: the registry safety parser (2026-11-10)
`trialtrace/parse/safety_parser.py` (`parse_safety`) reads the adverse-event module into `SafetyExtraction` (arms with death / serious / other counts, events with counts per group, threshold, time frame), contracts in `contracts.py`. Rules from D43: a missing number is None never 0; duplicate rows kept; threshold and assessment type kept; vocabulary and terms kept as written; titles starting total / overall / all / combined / pooled are flagged `looks_pooled` (never summed or dropped); no module = `absent`, not "no side effects"; a wrong type (text or true/false as a number, negative count, threshold not a number) is a `ParseError`. **Found on the real data and fixed:** one trial (NCT02733159) has a serious-event row with no term, only a note about deaths; it is now kept with `term = None` and `notes`, instead of failing the whole module. Result on the 391 stored trials: 258 parsed (616 arms, 13,880 events), 133 absent, 0 errors; 5 pooled-looking arms; 10 arms with at-risk 0. 34 new tests, 789 pass in total. Not yet stored (S3) or compared.

### D45. Safety S3: storing the adverse-event data (2026-11-10)
Migration `021_safety.sql`: `snapshots.safety_status` (ok / absent / NULL = never extracted), `safety_module` (threshold, time frame, description), `safety_arms`, `safety_events` (key includes kind and position, so duplicate terms are kept), `safety_event_counts` (own order); view rebuilt. `save_snapshot(..., safety)` fills in only while the status is unknown and never overwrites (same rule as outcomes and results); `get_safety` reads one version back in the registry's order (None = unknown, `absent`, or `ok` and empty are three different answers). Ingest step 5c parses safety; a bad module keeps the snapshot and records `safety_ParseError`. `python -m trialtrace backfill-safety` reads the raw files of versions stored before safety existed (no network; missing or unreadable files stay unknown). Real run: 392 stored versions: 259 filled (618 arms, 13,989 events, 36,181 event counts), 133 absent, 0 missing, 0 unreadable; a second run changed nothing. 13 new tests, 802 pass in total.

### D46. Safety S4: registry-only safety finding (2026-11-10)
`trialtrace/checks/safety_registry.py` (`check_safety_registry`): one finding per trial (check `safety_registry`, category `safety_registry_summary`) that states, per treatment group, deaths, participants with a serious adverse event, and the 3 most frequent serious events. Descriptive only: nothing is compared with a paper yet. **Why one finding, not three:** it is a plain fact about one table, and the per-quantity comparison findings come later (S7, S8). **Rules:** a number not given is written "not given", never 0; groups are never added up and a pooled-looking group is labelled; events with no term or a count of 0 are not named; equal counts keep the registry's order; no data (not extracted, no section, no groups) is `unknown` with its own reason. **Evidence:** `cited_values` is the single list of values the claim rests on; both the evidence rows (`safety_evidence`, written at ingest step 5c) and the finding's evidence IDs come from it, and the check answers `unknown` if any cited row is not stored. **Why evidence only for cited values:** evidence for every serious-event count would add about 52,000 rows; the cited values need 4,891. `backfill-safety` now also adds this evidence for versions already stored (built from the stored rows, safe to run twice). In review it is a plain fact (shown with `review --all`). Real data: 258 trials get a summary, 133 are unknown (no adverse-event section), every cited evidence ID is stored. One duplicate `save_evidence` in `repository.py` was merged into one (it now returns how many rows were new). 12 new tests, 814 pass in total.

### D47. Safety S5: code baseline reader for the abstract (2026-11-10)
`trialtrace/compare/safety_baseline.py` (`read_safety_statements`): no LLM. Per sentence it finds a **death** statement (death / died / mortality / fatal), a **serious** statement (serious adverse events, SAE) and one **event** statement per named event from a fixed list of 36 common events (spelling variants give one name, e.g. anemia = anaemia). Each statement keeps: the numbers written with it (as text), the grade if stated, `negated` ("no deaths"), `treatment_related`, the sentence number and the exact sentence with its offsets. **Number rule:** death and serious take every count or percent in the sentence; a named event takes only those in its own clause (up to the next comma or semicolon outside brackets), because abstracts list several events in one sentence. Not read as counts: decimals without % (ratios, p values), "95% CI", grades, time points, doses, phase and stage numbers. **Why keep `treatment_related` and the grade now:** the comparison (S7) must not compare treatment-related deaths with all deaths, or grade 3+ with "serious". No abstract = `no_text` (unknown), never "nothing stated". It only reads: nothing is stored or compared yet. **Found on the real abstracts and fixed:** "programmed cell death-ligand 1" is a protein, not a death; "5·4%" (middle dot) is one number; "vs." no longer ends a sentence (safety only; the frozen outcome baseline is untouched). Real run on 501 listed papers of trials with safety data: 225 have at least one statement (246 death, 62 serious, 320 event; 253 with numbers, 16 negated, 15 treatment-related), 6 have no abstract, 0 bad quotes. **Known weaknesses (the model reader in S6 must beat them):** an efficacy death (mortality as the outcome) looks like a safety death; "nausea, vomiting and diarrhoea (20%, 15% and 10%)" gives all numbers to the last event; no treatment group per number; only listed events. 26 new tests, 840 pass in total.

### D48. Safety S6, part 1: the model reader and its code checks (2026-11-10)
`trialtrace/compare/safety_llm.py` (`read_safety_with_llm`): **one model call per abstract** (not one per outcome as in 5b), asking only "which safety statements are in this abstract?". Each statement: type (death / serious / event), term, treatment group, count, denominator, percent, grade, treatment-related, cause (deaths), none-occurred, and an exact quote. **Why one call per abstract:** the question does not depend on the registry, so one answer serves every later comparison and costs far less. **Only statements with a number or an explicit "none"** are asked for, so every statement can be checked and later compared. **Code checks each statement:** the quote is in the abstract exactly (code sets the position and sentence number); every number the model gives is written in the quote (digits, or the words zero to twenty), so a calculated or rounded number is rejected; an event's term is in the quote; a treatment group is in the abstract; a count is a whole number. A statement that fails is kept in `rejected` with its reason, never used and never dropped; one bad statement does not cost the good ones. A protocol or review gives no statements (all moved to `rejected`). Broken JSON or a model error is `failed`; no abstract is `no_text` with no call. The prompt tells the model not to compare, judge or calculate, and that "severe" or "grade 3" is not "serious". `safety_prompt_hash` and `SAFETY_SCHEMA_VERSION` ("safety-read-v1") identify the configuration. **Known limit of the number check:** it proves a number is written in the sentence, not that it was given the right meaning (a count returned as a percent passes); the evaluation (S9) measures that. 40 new tests with a fake client, 880 pass in total. **No real model call has been made.** Part 2 (after the prompt is agreed): the `safety_matches` table, the run command with reuse of stored answers, and a small real run.

### D49. Safety S6, part 1b: prompt and checks tightened after an outside review (2026-11-10)
A review of D48 found six gaps; all are fixed in `compare/safety_llm.py` (schema now `safety-read-v2`, new prompt hash; nothing had been stored). **1. Number role, not only presence:** a percent must be written as a percent (`%`, "percent", or in a list ending in one: "12 and 15%"); a count and a denominator must be written as plain numbers; the denominator cannot be below the count; when count, denominator and percent are all given they must fit within 1 point (rounding). So `percent=42` for "42 (41.6%) of 101" is now rejected (this replaces the D48 "known limit"). **2. "None occurred" is checked:** the clause with the death / serious / event word must hold a word of absence (no, none, without, not, a written 0), "no difference in deaths" does not count, and none with a number above 0 is rejected. **3. Cause:** `cause = null` now means only "no specific cause stated"; a separate `all_cause` is true only if the quote says all-cause or any cause. **4. Grounding:** grade, cause, treatment-related and all-cause must be written in the quote, or the statement is rejected. **5. Attribution:** when one sentence gives several groups (or several events), the numbers must be written in the same order as their groups (or events); a swap, also in a "respectively" sentence, rejects those statements (`order_does_not_match`). A group named only in another sentence cannot be tied to its number by code: it is kept with `arm_in_quote = False`, and the comparison (S7) must treat that as an uncertain arm. **6. Untrusted text:** the abstract is passed between `<abstract>` tags at the end of the prompt, named as source text whose instructions are not followed; an abstract that itself contains the tags is not sent (`abstract_has_tags`). The code checks remain the real defence: an unwritten number cannot pass whatever the model was told. **Two prompt rules added:** count participants only, never event occurrences; no statements for a protocol, review or other document. Code now also sets aside statements from `other` (only `trial_results` gives statements; a later analysis of the one trial counts as trial results), and an answer with no valid document type is `failed`. **Still not provable by code:** that a count is participants and not occurrences, and attribution when the group is outside the quote; S9 measures these. 25 more tests, 905 pass in total. Still no real model call.

### D50. Safety S6, part 2: storing the readings, reuse, and the first real run (2026-11-10)
Migration `022_safety_readings.sql`: `safety_readings` (one row per paper, per system, per run: status, document type, model, served model, prompt hash, schema version, raw answer, tokens, `input_hash`, `reused_from`) and `safety_statements` (one row per statement, passed or rejected, with `rejected_code`). Insert-only; the model and the baseline are separate rows. **Naming:** the plan called the table `safety_matches`, but nothing is matched in this step, so the tables are named for what they hold. `trialtrace/read_safety.py` (`read_safety_for_trial`) reads each chosen paper (same choice and cap as `match-papers`) with both readers and stores both. Only trials with registry adverse-event data are read (nothing to compare otherwise). **Reuse:** a stored model answer with the same `input_hash` (abstract + model + prompt hash + schema + temperature) and the same served model is not paid for again: its **raw answer is checked again by today's code** (`reading_from_answer`), so a later improvement of the checks applies to old answers without a model call. A failed answer is retried, never reused; `reused_from` always points to the real call. CLI: `python -m trialtrace read-safety NCT... [--file] [--all] [--refresh]`. **Not done yet:** papers beyond the cap are named in the run output but not stored; quotes are not yet saved as evidence (both belong with the findings in S8). **First real run** (5 abstracts of 3 trials, `gpt-6.1-sol`, run `safety-20261111T024205Z`): 5 calls, 0 failed, 6,770 input and 3,071 output tokens, 40 seconds; the model gave 14 statements, all passed the code checks, 0 rejected; the baseline gave 7. A second run made 0 calls (5 reused). Read by eye: the model split two-group sentences into one statement per group with the right count, denominator and percent, kept "grade >= 3" apart, and returned nothing for an abstract with no participant counts; the baseline put both groups' numbers in one list. One sentence shows the limit of sentence-level grounding: "drug-related grade 3 or higher events occurred in 39.6% ..., with nausea most common (67.3%)" was read as drug-related nausea with no grade, which a person may read differently; such cases are for review. Five abstracts is a smoke test, not an evaluation. 14 new tests, 919 pass in total.

### D51. Safety S7: the comparison rules (2026-11-11)
`trialtrace/compare/safety_compare.py` (`compare_statement`, `compare_statements`): plain code, no LLM, nothing stored (findings come in S8). One checked model statement is set beside the registry table and gets a category and a reason in plain words. **Order: comparability first, numbers last.** (1) No registry data or a rejected statement is `unknown`. (2) **Definition:** a grade, "treatment-related" or one cause of death is `safety_not_comparable` (the registry counts every case, any grade, any cause). (3) **Group:** the abstract's group must match exactly ONE registry group by the words of its title (filler words such as "group" and "arm" ignored); no group stated is accepted only when the registry has one group; a group named in another sentence (`arm_in_quote = False`), no match or two matches is `safety_unclear`. Groups are never added up. (4) **Registry number:** deaths and serious events come from the group's row; a count the registry does not give is `safety_in_paper_not_registry` (not given is unknown, not zero). A named event is found by its name or a known spelling (the 36-event list of the baseline); no row with that name is `safety_unclear` (the registry may use another word); several rows are `safety_not_comparable` (one participant can be in both, never added); a row only in the serious list is compared only when the listing threshold is 0. (5) **Population:** the abstract must state its denominator and it must equal the registry's number at risk; otherwise `safety_not_comparable`. So 42 of 102 against 42 of 101 is not "consistent" (tested). (6) **Numbers:** counts must be equal; a percent may differ by 1 point (rounding): `safety_consistent` or `safety_number_differs`. Then per abstract: deaths or serious events the registry gives and no statement mentions are `safety_registry_not_in_abstract` (expected, low priority; named events are not listed this way). **Tolerances are written here before any evaluation.** **Choices that can be argued (decide before the S9 freeze):** a count with no denominator is not compared even when the counts are equal (strict, follows the plan; the reason still shows both numbers); "6 deaths" with no cause stated is read as all deaths. **Limits:** the time window is not checked (an abstract statement carries none; every answer says so); names are matched by words, not meaning, so "T-DXd" against "Trastuzumab Deruxtecan" is `unclear`. **Real data (read only, the 14 stored model statements of 3 trials):** all 14 are `safety_not_comparable`, 8 for a grade and 6 for treatment-related; no number was compared. That is the honest picture so far: abstracts mostly report graded or treatment-related events, which the registry table cannot confirm. 25 new tests, 944 pass in total.

### D52. Safety S8: one finding per safety quantity, in the report and the review queue (2026-11-11)
`trialtrace/compare/safety_rollup.py` (`check_safety_summary`): takes the trial's newest stored safety readings, compares every model statement with the registry (the S7 rules) and gives **one finding per quantity**: deaths, serious adverse events, and each named event (one name per event: "Anemia" and "anaemia" are the same). **Roll-up rule:** the category that most needs a person wins: number differs, then in paper not registry, then unclear, then not comparable, then consistent. So one difference in one paper is never hidden by agreement in another. **The two readers:** a quantity for which only the code baseline found a number is `safety_readers_differ` (for a person; the baseline cannot tell a safety death from an efficacy result). It is not raised when the model lists an event from the same sentence under another name ("infection/fever" against "infection"): found on the real data, where it gave two false alarms. When only the model found it, the finding says so in a note. **Left out by the abstract:** deaths or serious events the registry gives and no reader found are `safety_registry_not_in_abstract`, but only if every listed paper was read; with a failed reading or an unread listed paper the answer is `unknown`. Named events are never reported as left out. A trial with registry safety data and no reading gets one `unknown` ("not checked"). **Evidence:** each finding cites the abstract quotes and the registry values used (group title, counts, event term); `SafetyComparison.registry_values` records them, and `read-safety` saves them (`save_safety_evidence`, safe to run twice), so the report stays read-only. **Review queue:** number differs, in paper not registry, unclear, not comparable and readers differ are high priority; consistent and not-in-abstract are low (shown with `--all`), except a consistent finding where not every paper was read (normal). Per-statement reasons are listed under each finding. **Not done:** link review is unchanged; the card layout of section 11 belongs to S10; a changed abstract after the run is not detected (an unread paper is). **Real check (3 trials, 5 abstracts):** 12 findings: 5 `safety_not_comparable`, 7 `safety_registry_not_in_abstract`; every cited evidence ID is stored; a second evidence save added nothing. **Found here, fixed in D53 (affected 5b too):** all papers of a trial are downloaded in one file, so they share one raw hash, and the abstract evidence ID (`<NCT>:<hash>:abstract`) has no PMID in it. The whole-abstract evidence of a trial's second paper is therefore never stored under its own ID (107 stored files hold more than one listed paper), and two quotes at the same character positions in two papers would share an ID. 14 new tests, 958 pass in total.

### D53. The PMID is part of every abstract evidence ID (2026-11-11)
**Problem (found in D52):** PubMed returns all papers of a trial in one file, so they share one raw hash. The abstract evidence ID was `<NCT>:<hash>:abstract` (or `...:abstract[start:end]` for a quote), with no paper in it. So the second paper of a trial never got its own whole-abstract evidence (the same ID saved twice adds nothing), and a finding about paper B could point to paper A's abstract. Two quotes at the same character positions in two papers would also have shared one ID. **Choice:** the ID is now `<NCT>:<hash>:pmid<PMID>:abstract` (and `...:abstract[start:end]`); the evidence `field_path` is `pmid<PMID>.abstract`. One function builds it (`abstract_evidence_id` in `compare/paper_outcomes.py`), used by the outcome findings (5b), the outcome roll-up and the safety roll-up. **Old data:** evidence is insert-only, so old rows stay and older saved report runs still resolve their IDs; new report runs cite the new IDs. `python -m trialtrace backfill-paper-evidence` saves the evidence of the stored match runs and safety readings under the new IDs (no network, no model, safe to run twice). **Real run:** 133 rows added, a second run added 0; all 240 evidence IDs cited by the 440 paper and safety findings of 27 trials are stored, and every whole-abstract row equals its own paper's text. **Effect on the frozen evaluation (D42):** none on the numbers: only the evidence IDs changed, not statuses, categories or roll-up rules; the scorer reads the stored answers. `paper_rollup.py` has a new hash, recorded as amendment 3 in `evals/final/freeze.md`. **Test:** two papers in one file each get their own abstract evidence (`tests/test_paper_rollup.py`). 958 tests pass.

### D54. Safety S9, part 1: pool, draw and rubric draft (2026-11-11)
**Pool check first (as planned):** of 258 stored trials with registry adverse-event data, 148 have a listed paper with an abstract, 75 have two or more; 53 have 2 to 5 and were never read by `read-safety` (154 abstracts). That is above the "about 12 trials" floor, so the evaluation goes ahead. **Rule written before the draw** (`evals/safety/RULE.md`): the three trials the prompt was tried on are excluded; the pool is shuffled once (seed 20261111) and the first 18 trials are used; no abstract is chosen or dropped by its content, so abstracts with no safety statement stay in and test false alarms. **Draw:** `evals/make_safety_cases.py` gave 18 trials and 48 abstracts, and wrote per trial a blind sheet (numbered sentences, no system answers), the registry table (`registry.md`), the case list with text fingerprints, and an empty `labels.txt`; it refuses to run twice. **Rubric draft** (`evals/safety/rubric.md`, not frozen): one case is one safety statement in one abstract; extraction (model and baseline) and comparison are scored separately; a false "consistent" must be 0; routing counts labelled differences that reach review; a proposed keep rule (model recall at least 10 points above the baseline, precision at least 80%, no false "consistent", no missed difference). A person may label by meaning ("T-DXd" is "Trastuzumab Deruxtecan"), so the gap to the word-matching code is measured. **Not done yet:** labels, the freeze, the model run on these trials, the scorer. No model was called.

### D55. Safety S9, part 2: whole pool, written event definition, labels, scorer, challenge set (2026-11-11)
**Rubric agreed with the owner's changes:** one-to-one matching (one baseline statement with several numbers earns one label); routing with no labelled difference is "not tested", never passed; failing a gate means the model does not qualify as primary reader (provisional marking alone is not enough); a count with no denominator in the abstract is `safety_not_comparable`; "6 deaths" with no cause is extracted with `cause = null` and `all_cause = false` and may go through the comparison. **Amendment before the freeze** (`evals/safety/RULE.md`): the first 18 trials held only 19 statements, so the sample became the whole pool (53 trials, 154 abstracts), decided after seeing labels only and before any system output; the first 18 stay an identifiable subset. **Written definition of an eligible event** (rubric section 2), settled before the 35 added trials were labelled and re-applied to the first 18 (one statement added, a stillbirth): a named harm that happened to this trial's participants, with a number or an explicit none; a named class counts; not efficacy outcomes, not all events together, not withdrawals, not numbers pooled over several trials. **When several labels fit,** the first step that stops the comparison decides (definition, group, registry number, population, numbers). **Labels (drafted by Claude, blind to system answers):** 52 statements in 20 abstracts, 134 abstracts with none; 35 not comparable, 13 unclear, 2 in paper not registry, 2 consistent, 0 number differs. So the pool cannot test "number differs" and barely tests "consistent"; the scorer prints "not tested" for an empty branch and also prints how often the system answered "consistent". **One labelled case shows the cost of the strict denominator rule:** an interim paper says "no serious adverse events occurred" (no denominator) and the registry later lists 2; by the agreed rule it is not comparable. **Scorer** `evals/run_safety.py`: reads stored readings only (no model), extraction E1 to E5 for model and baseline, comparison C1 to C3, routing R1, Wilson ranges, the four gates, whole pool plus the two subsets; `tests/test_run_safety.py` (10 tests). **Challenge set** `evals/safety/challenge/cases.json`: 10 made-up abstracts beside made-up registry tables (18 statements: 9 consistent, 5 number differs, 4 not comparable) with explicit denominators; `evals/run_safety_challenge.py` scores it on its own, never mixed with the pool or the keep rule. Rules-only run (perfect reading, no model): 17 of 18 categories right, 0 false "consistent"; the one miss is the designed gap ("T-DXd group" against "Trastuzumab Deruxtecan" is `unclear` by word matching). **Spot-check:** `evals/make_safety_spot_check.py` wrote 30 abstracts (all 20 with a drafted statement, 10 drafted "none", shuffled) for a person to label blind. **Not done:** the human spot-check, the freeze, the single model run, the scores. 968 tests pass.


### D56. Safety S9 completed, with limits made explicit (2026-11-11)

After independent blind AI annotations were reviewed by Venu, labels were adjudicated before model output and
frozen: 57 statements in 20 of 154 abstracts across 53 trials, 134 empty abstracts. Exactly 154 pool and 10
constructed challenge requests completed without retries/errors. Pool extraction: 44/57 recall (77%), 44/52
precision (85%); category accuracy 27/57 (47%), conditional on all six scored fields correct 22/34 (65%).
Both of two labeled differences reached review. Zero false consistent occurred alongside zero consistent
predictions; no real-pool consistent/number-differs branch was tested. Challenge: 18/18 extracted and 17/18
categories, the known T-DXd name gap. The four original keep gates passed but were insufficient to establish
safety-report reliability. Records: `evals/safety/report.md`; a pre-call challenge PMID-format amendment is kept.

### D57. Optional baselines and bounded safety assistance (2026-11-11)

The owner chose LLM-first extraction and absolute report quality over baseline superiority. `read-safety` and
`match-papers` default to the LLM; `--with-baseline` enables the diagnostic reader. Existing Python defaults and
stored dual-reader runs retain compatibility. Reports support LLM-only rows without fabricated disagreements.
Specific blocking reasons, source-grounded group/event/window proposals, source-version fingerprints and
append-only human decisions are implemented. Unapproved matches never alter report categories; population
identity and collection windows require explicit confirmation before verified numeric verdicts. No reviewer
approval is fabricated. Safety extraction is v3 with target NCT, explicit study scope and named-class rules.
Three separate helper development calls gave two real abstentions and one constructed proposal; none was
approved, and these are not evaluation scores. Historical evaluated sources were archived before changes.

### D58. Explicit outcome attribution, independent holdout and registry wording audit (2026-11-11)

Outcome reader contract `paper-match-v2` supplies the target NCT ID. Its cache-key API requires a valid target ID
and includes it, avoiding cross-trial reuse of identical outcome/abstract text. Pooled or uncertain attribution
is unclear; another study's result is not identified. Tests verify prompt context and cache isolation, not model
semantic accuracy. No new outcome API calls were needed.

The owner chose to label the next safety set independently. Project-wide prior-case/model/helper exclusions
leave 48 trials / 285 clean abstract entries. Rule recorded before draw; seed 20261112 selects 30 trials / 58
abstract cases, capped at 3 randomly selected abstracts per trial, with an exact saved reserve order and inputs.
No content-based choice. If references have fewer than 60 statements, extend by saved 10-trial blocks before
freezing or stop at pool exhaustion. Blank statement and matching/scope references are at `evals/safety_v2/`.
The validator/freeze/run tooling rejects incomplete/unreviewed references, invalid candidate IDs, changed source
fingerprints and unsupported numeric gold verdicts. Scoring uses prelisted aliases, one-to-one pairing, errors
from unmatched helper proposals, explicit coverage and non-vacuous targets; it never simulates human approvals.
No model has run on this holdout; no result or freeze is claimed yet.

Read-only registry audit: 391 trials; 0 candidate same-role unmatched-title wording pairs, 33 ambiguous duplicate-
title items across 9 registered-versus-posted comparisons. Historical comparison unavailable for 390 trials.
No registry wording helper is added now. Missing version coverage prevents a general conclusion about rewording.
Full text and autonomous agents remain out of scope. See `docs/LLM_IMPROVEMENT_ITERATION.md` for the current
source/data state, limits, commands and validation prerequisites.

**D58 validation:** 1,018 regression tests passed. Holdout source/exposure guards and previous safety artifacts were verified; no new model API calls. The unseen evaluation remains pending independent human references.


### D59. Match evaluation paper selection to the production 10-paper cap (2026-11-11)

The owner identified that the preparer's random three-abstract cap did not represent production. Before any
reference rows, freeze or model calls, the same 30 trial IDs/order were retained and paper selection was amended
to the shared product selector: newest listed abstract per PMID, lowest numeric PMID first, at most 10. The old
packet is archived in `evals/safety_v2/amendments/before_ten_paper_selection.zip`, with the reason recorded before
source replacement. Existing reference files were preserved; nonempty references would have stopped amendment.

Revised workload: 127 selected abstracts and 73 skipped at the cap. Four selected papers appeared in older
project sheets, though neither product model pipeline read them. Keep them for exact production coverage, but
exclude them from strict unseen scoring (123 cases). Twenty-six trials have entirely unseen selected sets.
Per-trial results, equal-trial-weighted extraction metrics and recorded coverage supplement pooled results.
The 60-statement extension rule counts strict unseen references only; reserves follow the new selection too.
No content-based redraw or new model call occurred. This does not implement the separate outcome-identification
and semantic multi-paper outcome-roll-up evaluation; those still need their own references and scorer.

**D59 verification:** 1,021 tests passed. Every selected/skipped PMID matches production; archived trial order and human reference files are unchanged. No new model calls, freeze or model run.

### D60. Safety v2 holdout: drafted references, owner review, required extension; and a doc check (2026-11-11)

**Labels for the first 30 trials.** The owner asked Claude to draft the v2 references. For all 30 trials (127 abstracts,
every sentence read, no harm-word filter) `labels.txt` and `match_labels.jsonl` were filled: 43 statements in 14
abstracts, 113 abstracts with none; 37 statements are in the strict unseen set (6 are in coverage-only papers); 42 not
comparable, 1 unclear, no consistent or number-differs case. **Provenance, stated plainly:** these are labels drafted
by an AI and then reviewed and accepted by Venu (recorded with file hashes in
`evals/safety_v2/amendments/reference_review_initial_30.json`). They are not labels written independently by a person,
which is what the protocol first planned; the same model family also drafted the earlier 53-trial labels. The choices
most worth a second look are in `evals/safety_v2/draft_label_notes.md` (disease outcomes the treatment is meant to
prevent were not counted; cumulative-incidence percents were; numbers pooled over trials were not; a denominator in an
earlier clause of the same sentence was not carried over). **Why nothing is comparable:** v2 needs a denominator in the
sentence and a verified equal collection window before a number is compared, and no abstract gives both. Three cases
the rules hide are noted for a person (NCT00006164 serious-event percents that do not match the registry; NCT04302545;
NCT04770532). **Extension, by the prewritten rule:** 37 strict unseen statements is below 60, so the next 10 reserved
trials (42 abstracts) were added before any model call; they are not labelled yet (`evals/safety_v2/NEXT_LABELS.md`),
the review record for the whole set is open again, and the tool refuses to freeze or run. No model has been called on
this set.

**Doc check.** All docs and `PLAN.md` were compared with the code and files. Corrected: `README.md` (safety status,
readers, commands, evaluation, file list), `WORKFLOWS.md` (5b and safety diagrams and table were still marked planned),
`PROBLEM_AND_POSITIONING.md` (safety was "not built"), the `PLAN.md` phase table row for safety, the test count in
`LLM_IMPROVEMENT_ITERATION.md` (1,021), and the v2 label status everywhere. Verified unchanged: CLI commands and
`--with-baseline`, `safety-read-v3`, `paper-match-v2`, the 10-paper cap, the S9 numbers against `evals/safety/report.md`,
the registry audit counts, 1,021 tests passing.

**Found during the doc check and fixed:** `load_report_comparison` in `trialtrace/safety_assist.py` chose the newest
human review by timestamp only. Two reviews saved in the same clock tick tied, the first one won, and a later
rejection could be ignored (`test_later_rejection_revokes_an_approved_mapping` failed about 5 runs in 6 on Windows).
The review file is append-only, so the line order now breaks the tie: the later line wins. 1,021 tests pass, the
helper tests 8 runs in a row.

**D60 update, same day: the 10 added trials are drafted too.** Claude drafted `labels.txt` and `match_labels.jsonl`
for the 10 extension trials (42 abstracts): 26 more statements. The whole v2 set is now 40 trials, 169 abstracts,
69 statements (64 not comparable, 3 unclear, 2 in paper not registry; still no consistent or number-differs case),
and `--check-labels` passes. **Strict unseen statements: 53, still below 60,** so by the prewritten rule the last 8
reserve trials must be added (or the pool is exhausted) before freezing. These extension labels are AI drafts that the
owner has not reviewed yet; `reference_review.json` is untouched. Choices to check are appended to
`evals/safety_v2/draft_label_notes.md`. No model was called.

**D60 update 2, same day: verification applied, pool exhausted.** `evals/safety_v2/label_verification.md` raised two
points on the extension drafts and both were accepted: NCT01855048 is now `safety_unclear` (group unresolved comes
before the missing denominator), and the ten NCT02399085 rows now have window `unknown` with no quotes (a data-cutoff
date against a maximum duration does not establish the window relationship). The same no-group pattern exists in three
rows of NCT03971422, which belongs to the 30 trials whose review is already recorded; those files were not changed and
the point is left to the owner. Because 53 strict unseen statements was below 60, the last 8 reserve trials were added
(`--extend`) and drafted by Claude. **Whole v2 set: 48 trials, 188 abstracts, 80 statements (73 not comparable, 5
unclear, 2 in paper not registry), 62 strict unseen; the reserve is exhausted; `--check-labels` passes.** Still no
consistent or number-differs reference, so those branches will be reported as not tested. The 18 extension trials are
AI drafts not yet reviewed by the owner; `reference_review.json` is incomplete, nothing is frozen, no model was called.

### D61. After safety v2: reader scope v4, inexact numbers rejected by code, helper window and role-name rules (2026-11-11)

**Problem (from the two read-only audits in `docs/SAFETY_V2_RESULTS.md`).** Precision was 57 of 87. Twenty of the 30
extra statements were disease outcomes the treatment is meant to prevent, read as harms; ten were baseline
characteristics, inexact numbers, a vague "none" and a therapy. All 11 wrong helper proposals were for those extra
statements. All 15 helper window claims compared an analysis time point with the registry's collection window. Three
helper packets failed because a group was named only "control group".
**Choice.** (1) The reader prompt states the scope the references already used (`safety-read-v4`). (2) Code rejects a
count or percent written as a bound, an estimate or a range (`number_is_inexact`), because that one is checkable
without a model. (3) The helper keeps a window claim only when its abstract quote is about safety collection; code
sets other claims to "unknown" and records that it did (`limit_window_claim`). (4) A role name may be grounded by the
sentence that defines the group; a treatment name must still be in the quote (`safety-match-v2`).
**Cost.** The prompt wording was fitted to cases already seen, in two rounds (the first round over-excluded and lost
4 labelled statements). The v2 set is therefore development data now and cannot test v4. The role-name relaxation
removes one code check for control groups; a person still has to approve every match.
**Kept unchanged.** The frozen v2 references, scores and per-case outputs; the evaluated source is archived in
`evals/safety_v2/frozen_evaluation_source.zip`.
**Tests.** 7 new tests (inexact numbers, the prompt's scope phrases, window claim kept or set to unknown, role name,
treatment name still required); 1,028 pass. **Next.** New trials and new reviewed references are needed before any
claim about v4.


## D62 ? Separate estimates, require local trial attribution, and acquire fresh evaluation cases (2026-11-12)

**Problem.** Time-to-event percentages can be mistaken for raw participant proportions. Multi-trial papers can assign another trial's numbers to the target. Rejected model statements previously could still leave a registry-not-in-abstract summary.

**Decision.** safety-read-v5 retains explicit cumulative-incidence/probability and Kaplan-Meier estimates with measure=time_to_event; code blocks numerical comparison with registry adverse-event counts. Approximate/bounded values remain excluded. Multi-trial text requires an exact local source span of at most two sentences containing the result and target NCT ID, no other NCT ID, and no pooled-result wording. Distant or ambiguous attribution is rejected and retained with a reason. This conservative rule can lose legitimate remotely attributed results and is not proof of semantic attribution.

**Storage/reporting.** Migration 023 preserves measure and attribution evidence across saved reports. The helper cannot override measure mismatch. Any rejected statement makes the paper's model coverage incomplete, preventing unsupported omission claims.

**Fresh evaluation.** A fixed metadata-only API draw predates new abstract inspection: completed phase 3 trials with results, posted by 2023-12-31, first 500 ordered by update, remove already stored trials, seeded selection of 60. Registry/PubMed receipts include four registry failures and 21 no-paper cases. The eligible pool has 31 trials; initial seeded selection has 30 trials / 79 abstracts (76 strict unseen, three production-coverage-only), one reserve. Prior v2 papers/text are excluded from strict scoring. References are blank; no approval, freeze or v3 model score is fabricated.

**Evaluation additions.** Separate human measure references and scoring; any observed wrong helper proposal fails the zero-error gate even below its coverage minimum. Acquisition, protocol, and review links are in evals/safety_v3 and evals/safety_v3_acquisition. Missing real numeric branches must remain not tested. Historical frozen v2 reference hashes were checked unchanged. Its archived source remains available.

**Next.** Complete independent reference labels/review, then apply the fixed extension rule, freeze, and run once. The naturalistic set may still lack numerical agreement/disagreement cases; unit tests cannot establish real-world reliability.

**Validation.** 1,043 tests pass (15 new since D61), including local attribution, pooled/other-trial rejection, measure classification, database round-trip, measure-reference completeness and the wrong-proposal gate. Migration 023 applied. The actual blank v3 packet correctly fails label validation; no freeze or model marker exists.


## D63 ? Complete the reviewed, frozen safety v3 run (2026-11-12)

Venu explicitly accepted the complete corrected references. Recorded review hashes, validated 69 strict unseen statements, froze, and ran once on 30 trials / 79 abstracts (76 strict unseen cases). There were 105 calls / 105 transport attempts with gpt-6.1-sol. Freeze hashes verified after completion; evaluated source archived and run receipt retained. No model answers caused a reference change.

Strict results: recall 64/69 (93%), precision 64/70 (91%), six-field agreement 57/64 (89%), category 47/69 (68%), helper candidates 7/12 correct, zero asserted windows, 2/2 labelled in-paper/not-registry differences routed. Absolute reliability gates are not met; no numeric-agreement/disagreement or time-to-event validation is available. These are different cases from v2, not a controlled improvement comparison.

The source audit separates code-rejected valid statements, unresolved trial attribution, subgroup information loss and reference granularity/severity interpretation. No scores or labels were changed after the run. Details: docs/SAFETY_V3_RESULTS.md and evals/safety_v3/run/error_audit.json. Next: development fixes followed by new independent validation; preserve this holdout as evaluated history.


## D64 ? Repair guards, subgroup semantics, trial identity and event scope (2026-11-12)

Implemented safety-read-v6 / safety-match-v3. Phase numbers no longer imply a multi-trial abstract; event ordering uses local clauses and retains uncertainty for remote group mentions. An intervening relative subgroup is not used as the percentage denominator; actual arithmetic contradictions still fail. Treatment and population are separate, preserved by migration 024. Matching an arm does not establish population equivalence. Human verification remains explicit and context-bound.

Registry/abstract design conflicts require trial-identity review before using target-specific harms or helper matches. Historical placebo-to-active cohorts and blinded phases with extensions are distinguished from a current placebo arm. These are narrow contradiction checks, not a general identity classifier. The helper abstains on ambiguous seriousness strata; explicitly scoped events can match one stratum without addition. Evidence-group and total/component metadata preserve overlap rather than deleting clinically supported detail for a better score.

Development replay: 79 stored answers, zero model calls; all five prior misses recovered, no previously matched statements lost; all five ambiguous original helper proposals blocked. The first broad identity check over-excluded five valid statements and was refined with positive tests; both attempts are retained. Frozen references/results are unchanged. No new model has validated the revised prompt. Full details and prospective evaluation policy: docs/SAFETY_REPAIR_ITERATION.md.

**Verification:** 1,068 tests pass (25 new since D63); migration 024 applied. Frozen v3 references and retained raw-answer/score receipt hashes checked unchanged. No new model calls.


## D65 ? Prepare independent safety v4 validation, with source support separated from target attribution (2026-11-12)

The user authorized the next independent validation. The production v6 reader/v3 helper are unchanged from D64; source and settings were locked before labeling. A new metadata-only registry draw excludes every already stored trial ID. Sixty new trials were acquired, 40 had papers and 20 did not; the eligible pool contains 39 trials. Seed 20261113 selects 30 trials / 70 abstracts (67 strict unseen; three coverage-only), with nine reserved in saved order. All selected cases use the production first-ten rule. The naturalistic stopping rule is 60 strict target statements or reserve exhaustion, not a fixed abstract count or performance-based redraw.

A separate constructed packet has 40 fictional cases: ten agreement, ten disagreement and four each for population, identity, seriousness, measure and granularity. Its initial 28 sources were retained; twelve numeric examples were appended before labels/model output to reach branch minima. This amendment is recorded. Constructed results cannot be pooled with naturalistic evidence.

Twenty real abstracts were selected mechanically for independent labeling before any AI drafts. No gold labels were drafted anywhere in this packet. The complete source inventory records other/pooled/unresolved attribution separately; a grounded other-trial number cannot count as target correctness. Reference fields now include population, cause/all-cause, event scope and detail role. Known population/window relationships need source quotes; equal denominators are insufficient. Totals/components share claim-family references rather than being silently dropped or added.

The new runner checks sources, labels and actual independent review, applies the real identity guard, freezes code/environment, runs once, and retains all raw answers/proposals. It separately supports rescoring after actual human match reviews; no oracle approvals are simulated. Matching is one-to-one; source fact support, target extraction, full-field agreement, routing, helper accuracy and per-trial/macro scores remain distinct.

Verification: 1,079 tests pass, including 11 new cases for review guards, pre-label system lock, source fingerprints, scope proof, matching, wrong-arm/other-trial claims, constructed coverage, once-only execution and no fabricated approval. All 110 prepared sources validate; unlabelled references block the next stage. No model calls, labels or freeze were fabricated. Venu's independent 20-case labels are the necessary next step. Links: docs/SAFETY_V4_VALIDATION.md and evals/safety_v4/START_HERE.md.


## D66 ? Complete v4 reference drafts and preserve independent-versus-AI authorship (2026-11-12)

Venu requested checking INDEPENDENT_LABELS.md and filling the remaining labels, then explicitly confirmed independently authoring the original 20 notes. The contradictory earlier boilerplate is superseded; the exact submitted file is archived with its hash. The original prose decisions remain available. AI transcriptions/checks and proposed corrections are separately attributed; no full human acceptance is recorded.

All 70 naturalistic abstracts were read in full without a vocabulary filter. Structured references are filled for those plus 40 constructed cases. Naturalistic source inventory: 101 rows, 76 target (75 strict unseen), five other, 13 pooled, seven unresolved. Target categories: 56 not comparable and 20 unclear. Constructed inventory: 45 rows, 43 target, including 11 consistent and ten differing. No reserve extension is needed. These are reference counts, not model scores.

A registry-source check identifies NCT03062280 as DIUR-006, an extension accepting feeder-study completers. The explicit phase-3 MR-HC zero is retained in source inventory but proposed as other-trial evidence. Registry official title/design/summary and raw-file hash are saved for review. Other checks preserve unresolved BE HEARD identity, exclude borrowed KEYLYNK denominators, retain the explicit single death's GI-bleeding cause and decline to equate ovarian dysfunction with one ovarian-failure stratum. All new interpretations remain drafts pending Venu's review; labels were not fitted to model outputs.

Validation: all 110 case references pass source/schema/candidate checks, with 75 strict target statements; all 11 v4 tests pass again. The locked reader/helper production configuration and retained v3 run hashes are unchanged. No reader/helper calls, freeze or simulated approvals occurred. Review packet: evals/safety_v4/LABEL_REVIEW.md; decisions/limits: DRAFT_LABEL_NOTES.md; summary and attribution records are beside them. Next: actual full structured-reference acceptance, then freeze and one evaluation run.


## D67 ? Freeze and complete safety v4 evaluation (2026-11-12)
Venu accepted all structured references before freezing. The single run is complete: strict real recall 65/75 (87%), precision 65/66 (98%), all-field agreement 43/65 (66%); helper agreement 29/32. Constructed recall/precision 42/43, window assertions 21/21 and difference routing 10/10. No verified numeric comparisons exist before actual human match review. Current results do not qualify the system as a dependable safety comparison reporter. See [Safety v4 results](SAFETY_V4_RESULTS.md) for error analysis, scoring limitations, authorship and next steps. Frozen labels, source and original scores remain unchanged.


## D68 - Repair v4 scope guards and detail level; development replay only (2026-11-12)
Two guard faults found in the v4 audit are fixed in `safety_llm.py`: "any grade" now counts as all grades, and an event named after a lead phrase such as "Serious ... events were reported, with the most common being X" inherits "serious" (a "non-serious" wording still blocks it). A serious-event or death count with no event name and no cause is now recorded as a group total. Helper prompts and proposals are unchanged, so the wrong helper proposals remain; every proposal still needs human review.

Offline replay of the 110 saved v4 answers (no model calls, `python -m evals.replay_safety_v4_guards <name>`, output in `evals/development/safety_v4_guard_replay2`): strict real recall 74/75, precision 74/75, all-field 53/74 (72%); constructed all-field 40/42. The replay reproduces the frozen 65/75 and 65/66 first, so it is faithful. The v4 data is now development data: these numbers show the repairs work, not that the system is validated. The arm, population and component-versus-total gaps remain convention questions. Frozen v4 scores, labels and archived source are unchanged; `items()` gained a `check_lock` switch only so the replay can load them. Tests: 1,085 pass.


## D69 - Prepare safety v5 coverage draw (2026-11-12)
Rule written first (`evals/safety_v5_acquisition/RULE.md`, Amendment 1 explains the 60-to-100 ingest). 100 unseen trials ingested; 52 eligible (posted safety plus a clean abstract). A code-only screen (no model) finds 15 trials with a numeric or window candidate (14 numeric, 6 window). That is below the 30 the rule allows, and the screen is loose (a name match with a registry row, not proof of comparable arms or windows), so v5 may still leave real numeric and window branches thin. Nothing is labelled, frozen or run yet. Next: choose the sample from screen.json by the rule, then independent labelling.


## D70 - Build safety v5 coverage-enriched packets (2026-11-12)
Per the rule in `evals/safety_v5/RULE.md`: all 15 screen candidates plus 15 seeded other trials = 30 trials, 77 abstracts (all strict unseen), 20-case blind subset. System code locked before labelling (`system_candidate.json`, includes D68). References are blank; no AI drafts exist; no model called. Reported as an enriched sample, not prevalence. Next: independent labels for the blind subset (`evals/safety_v5/BLIND_SUBSET.md`), then the rest, then a v5 runner and freeze.


## D71 - Extend safety v5 after the 57-statement count (2026-11-12)
The 77 v5 abstracts were AI-labelled at the owner's request (see `AI_LABEL_RECORD.json`, `reference_review.json`): 57 strict target statements, below the 60 minimum; no confirmed agreeing or differing numeric comparison. Per the rule, the next 10 reserve trials were added (40 trials, 95 abstracts, 12 reserve left). The 18 new abstracts have blank references and the full review stays open. The AI-authorship record stands: these are not independent human labels, and no freeze or model run has happened. `LABEL_NOTES.md` has a heading that says "Venu-authored"; its own text says AI draft, and the review record is the authority.


## D72 - Finish v5 reference drafts; record review honestly (2026-11-13)
The first extension (59 statements, still short) was followed by a second under the same rule: 40 -> 50 trials, 107 abstracts, 65 strict target statements (60 minimum met). The 30 extension cases were AI-drafted from sources only (`AI_EXTENSION_LABEL_RECORD.json`). Venu reported reviewing the first 77 labels; Venu then stated the first 77 labels are Venu's own after review, and they are recorded that way; the blind-first flags stay false because the blind 20 were not labelled before any draft existed. The 30 extension cases still await Venu's review. Only 7 of the new statements are target-attributable, all unclear or not comparable: still no agreeing or differing numeric comparison. Freeze is blocked until review is recorded and the blind-subset gate is resolved.


## D73 - Replace the v5 blind subset with fresh unread abstracts (2026-11-13)
Option chosen by the owner. 60 more trials were ingested (160 of the saved draw), and code picked 20 fresh trials by seeded shuffle (RULE Amendment 2): 59 fresh abstracts, 20 of them the blind subset, 11 eligible trials unspent. No abstract was read or drafted by the assistant. The old blind 20 remain ordinary strict cases; the replacement is logged in `label_amendments/blind_subset_replaced.json`. The 107 earlier cases keep their review; review is reopened for the 59 new ones. Next: Venu labels the fresh 20 blind, then the other 39 are labelled and reviewed, then freeze. Coverage of real numeric and window comparisons is still unmeasured.


## D74 - v5 references complete and reviewed (2026-11-13)
All 166 v5 cases validate with 94 strict target statements (the first 86 plus 8 added in Venu's review of the last 39). Venu reviewed every file and states the labels are Venu's own; recorded in `VENU_FRESH_REST_REVIEW_RECORD.json` with hashes. The fresh abstracts were drafted before review, so the blind-before-draft flags stay false and the freeze gate still refuses. Still no real agreeing/differing numeric comparison in the references. Freeze needs the owner's decision on a written blind-gate exception.


## D75 - Safety v5 frozen run (2026-11-13)
v5 froze and ran once: strict recall 78/94 (83%), precision 78/103 (76%), all-field 37/78 (47%), helper 17/21, window claims 0/2 supported, 12/12 in-paper-not-registry differences routed, no consistent or number-differs outputs. Targets not met; numeric and window branches still untested. The D68 replay figures did not generalise. Owner waiver recorded for the blind-order flag; results state the references were reviewed and adopted by Venu with AI drafts preceding review. See [SAFETY_V5_RESULTS.md](SAFETY_V5_RESULTS.md).


## D76 - Class-level fixes from the v5 audit, checked on a few abstracts (2026-11-13)
Instead of repeating full evaluations, the v5 failures were fixed by class and checked small: offline replay of the 166 saved answers plus live reader calls on 14 abstracts. Fixes: reader safety-read-v7 (stent thrombosis is the efficacy outcome in a drug trial meant to prevent it; descriptive prevalence and management steps are not harms) and two code guards (events for two groups listed in different orders pass the order check; "one of two trials" is no longer a multi-study abstract). Results, all development data now: offline replay recall 85/94 (was 78) with no new extras; live reader on 9 abstracts, extras 18 -> 0 with one more match and no losses; five more abstracts, extras 5 -> 2. Tests: 1,087 pass. Not fixed: hypotension component rows, grade-split rows named "cases", the RCT-subset death percentage, upper-GI comparator (label/representation questions). These numbers are not validation: the cases were used to design the fixes. Next: a clean validation on trials the fixes never saw.


## D77 - Helper window guard; v5 helper "errors" are mostly label gaps (2026-11-13)
Code guard: a same/different window claim is set to unknown unless the abstract quote gives a length of time ("12 weeks", "day 28", "up to", "until"). Replay of all saved window claims: v4 kept 21/21, v5 dropped the 2 wrong "different" claims ("during the treatment period"). 1,088 tests pass. Audit of the v5 helper "wrong" event proposals: the v5 labels record registry event ids for only 1 of 59 event statements (v4: 12 of 60), so any proposal scores wrong. The rash, conjunctival hyperaemia and punctate keratitis proposals look like exact name matches; they need Venu's review, not a code change. Frozen scores stand. Also repaired garbled punctuation (? for dashes and quotes) in SAFETY_V4_RESULTS.md; the same fault is in six other docs.


## D78 - Owner convention: a non-serious-only registry row cannot match an unrestricted event (2026-11-13)
Venu reviewed the four v5 helper event proposals (rash x2, conjunctival hyperaemia, punctate keratitis) and marked all Wrong: the abstract gives no seriousness restriction, "mostly mild or moderate" is severity not seriousness, so a non-serious-table row is narrower. Code now turns such proposals into abstentions (`event_row_non_serious_only`); the original proposal stays in the saved reason for audit. Replay of saved helper event proposals: wrong ones fall from 8 to 1 (the serious-only T18 case); correct ones fall from 8 to 2, because the frozen v4 labels counted six same-pattern exact-name matches (nausea, fatigue, hypophosphataemia, headache, injection-site reaction, arthralgia) as Right. Those v4 labels and scores are kept as frozen; under this convention they would be Wrong, so v4 and v5 helper scores are not directly comparable. Cost: fewer helper suggestions, reviewers match more by hand. Not applied to serious-only rows.


## D79 - Conventions fixed and system locked before the clean validation (2026-11-13)
Owner conventions written to evals/safety_v6/CONVENTIONS.md before drawing any v6 trial: seriousness applied symmetrically (an unrestricted statement matches neither a serious-only nor a non-serious-only row), numbered components kept with their own denominators and never added to the total, "cases" count as participants only when the text says people, equivalent arm/population wording accepted through reviewer-listed aliases. Reader is now safety-read-v8 (two prompt rules). Live four-abstract mini-check (development data): matches 9 -> 11, extras 5 -> 5; the hypotension rule changed nothing (1 of 3 still matched, 2 extras), and the "cases" rule cost two tofacitinib matches that the frozen labels counted, as expected under the new convention. So v8 does not clearly improve extraction; the gain is in the rules, not the numbers. New evals/helper_workload.py reports proposal accuracy, abstention rate and manual matches left from saved answers (pre-guard baseline: v4 38/43 accurate, 7/109 abstained, 117 manual matches left; v5 17/21, 41/103, 151). System lock with file hashes: evals/safety_v6/system_lock.json; 1,091 tests pass. The v6 reference labels must come from a reviewer who did not develop the reader.


## D80 - v6 clean-validation batch prepared, labels blank (2026-11-13)
Rule written before ingest (evals/safety_v6_acquisition/RULE.md): first 80 not-yet-stored trials from the saved v5 reserve order, ingested metadata and papers only, no model. Code-only screen: 49 eligible, 16 numeric and 6 window candidates. Sample: first 12 candidates plus first 8 other eligible trials in saved order = 20 trials, 54 abstracts (48 strict). Enriched, never natural prevalence, never pooled. System candidate locked before labelling (evals/safety_v6/system_candidate.json and system_lock.json). Reviewer: Venu, writing every label from abstract and registry alone; independence limited (Venu did not write the reader but has seen the v4/v5 audits and the conventions), recorded in the rule. No AI drafts exist. Next: Venu fills the 54 reference files, then structure check, hashed freeze, one run.


## D81 - v6 labels adopted by Venu; saved extension rule applied (2026-11-13)
The 54 v6 references were checked for structure (valid, 39 strict target statements). LABEL_PREPARATION_RECORD.json says a language model prepared them at Venu's request; Venu states they are Venu's reviewed labels (VENU_REVIEW_RECORD.json, hashes kept). Provenance for results: prepared by a language model, reviewed and adopted by Venu; not blind-first, not fully independent; owner waiver flag set as in v5. The freeze gate refused (39 < 60 strict target statements, reserve not empty), so the saved extension rule ran: next 10 reserve trials added, review reopened (prior review kept in review_history.jsonl). Venu must label the added abstracts; then freeze and one run.


## D82 - v6 one-time larger extension, to avoid repeated labelling rounds (2026-11-13)
At the owner's request the saved 10-trial extension steps were replaced by one larger step (RULE.md Amendment 1, counts only): 40 more trials ingested from the saved reserve order, code-only screen (14 eligible), then all 12 remaining reserve trials plus the first 12 eligible new trials added at once (evals/extend_safety_v6_once.py). Total v6: 54 trials in 3 groups, 123 abstracts (first 54 labelled and adopted by Venu; 69 blank: 17 from extension 1, 52 from extension 2). The reserve is now empty, so no further extension rounds exist; the freeze gate no longer asks for one. If the final strict target count is below 60 it is reported as a coverage limitation. Frozen v4/v5 untouched.


## D83 - Safety v6 frozen run on unseen trials (2026-11-13)
Locked system (reader v8) run once on 123 abstracts / 54 trials, 61 strict target statements. Recall 43/61 (70%), precision 43/50 (86%), all-field 30/43 (70%), helper 24/26, windows 0/3, no numeric comparisons. Targets not met. Audit: 8 of 18 misses are guard rejections (`related_not_in_quote` for "associated with", `none_not_in_quote` for multi-term "no reports of"), 5 misses plus 5 extras are an MI naming convention mismatch, 2 are a fracture scope choice. The v5 dev gains did not generalise, which supports tuning by class then validating on fresh data. References: language-model prepared, reviewed and adopted by Venu; not independent. See [SAFETY_V6_RESULTS.md](SAFETY_V6_RESULTS.md). v6 is now development data; next fixes need a new unseen set.


## D84 - Multi-paper cross-check on 13 more trials (2026-11-13)
**Corrected in D91: one of the 13 trials (NCT03566667) was also in the D42 set, so 12 trials are new. Without it: 50 outcomes, outcome recall 22/22, precision 22/27 (81%), same 5 overclaims. The figures below include the duplicate.**
Smoke check that the multi-paper pipeline still works, requested while safety is parked. Rule written first (evals/multipaper_check/RULE.md): trials from positions 80-150 of the saved final draw, never labelled or scored before (same eligibility as D42), 13 trials, 144 pairs. Labels drafted by Claude from abstracts only under evals/rubric.md and its addendum, frozen by hash (evals/multipaper_check/freeze.md) before the run; not human-verified. One fresh run (match-20261113T160050Z, gpt-6.1-sol, 144 model calls, 0 reused, `--refresh`). No code was changed. Nothing broke: 0 failures, 0 bad quotes. Level A model identification 107/120 (89%); outcome level model recall 26/26, precision 26/31 (84%); baseline recall 5/26, precision 5/9. The 5 overclaims (truth not yes) all went to review, 0 missed; 24 of 26 correct ones also went to review. Consistent with D42 (22/26 and 22/26). Development cross-check only: single AI labeller, small counts, not pooled with D40/D42.

## D85. Dashboard review queue (2026-11-13)

- Built: `dashboard/app.py` (Streamlit). Enter an NCT ID; stored outcomes, linked papers and findings show for free; Analyze Trial runs ingest, ingest-papers and match-papers (sidebar cap on papers per trial). The page labels every finding AI-identified until reviewed.
- Decision: reviews of model findings are saved in a separate insert-only table (`paper_match_reviews`); the model finding in `paper_matches` is never changed.
- Decision: a review is found again by `input_hash` (the question), so it survives a rerun that reuses the answer. If the model gives a different answer to the same question, the review no longer counts and the finding needs review again.
- Decision: "Correct" only proposes a correction (still Needs review). It becomes Human-verified after an explicit "Accept the correction". "Unable to determine" stays Unresolved and needs a note.
- Check: Analyze Trial on NCT01133626 (1 outcome, 1 paper, cap 1): first click 1 model call, second click 0 (reused). Tests: 1,103 passed.
- Limit: the queue shows the latest run of one trial; the summary counts are per trial. Review controls were tested through Streamlit's test runner, not by hand in a browser.


## D86. Safety (question 6) is parked (2026-11-13)

Decision (owner, 2026-11-13): stop safety work for now and move to the dashboard and the paper-evidence arm. Safety stays in the code and in the report as it is, but it is not validated and is not to be presented as a working comparison.

**Why parked**
- Six evaluation rounds (v2 to v6) never met the reliability targets. The clean v6 run on unseen trials gave recall 43/61 (70%), precision 86%, all-field agreement 70%.
- Gains found by tuning on one set (v5 replay) did not carry to the next set (v6): fixes were tuned on the data they were measured on.
- The parts that matter most are still untested on real data: verified numeric comparisons (0), and supported time-window assertions (0 of 3 in v6, 0 in v2 and v3). Real agreeing or differing numbers did not occur in any sample.
- Every set is small, enriched and labelled with help from a language model, so intervals are wide and each round costs a lot of labelling time.

**What works and is kept:** registry safety parsing and storage, the LLM statement reader with code-checked quotes (reading is usable: recall 70 to 93% depending on the set), the comparison rules (tested with constructed cases), and human review of helper proposals.

**Open problems, if resumed (found by the v6 audit)**
1. Guard `related_not_in_quote` rejects valid "most common adverse events associated with ..." sentences.
2. Guard `none_not_in_quote` rejects valid multi-term "no reports of A, B or C" sentences.
3. Naming rule for myocardial infarction variants (protocol MI, universal/ARC MI versus periprocedural MI).
4. Scope rule for efficacy-type events such as new clinical fractures.
5. Proposed, not confirmed by the owner: narrow the first product scope to deaths and serious-event totals, which are the easiest to compare and the most useful.

**Resume conditions:** fix 1 to 4 in code; settle the naming and scope rules in `evals/safety_v6/CONVENTIONS.md`; validate once on a fresh, unseen set with the rule written first; never reuse v2 to v6 as unseen (all are development data). Frozen results (v2 to v6) are unchanged and stay as the record.

**Not affected:** the outcome arm (D42 and the D84 cross-check) and the registry checks. See [SAFETY_V6_RESULTS.md](SAFETY_V6_RESULTS.md).


## D87. Dashboard cross-trial summary (2026-11-13)

- Built: an "All trials" tab beside "One trial". Three counts (Needs review, Human-verified, Unresolved) cover the latest run of every stored trial (39 trials, 445 findings at the time); each count opens its findings; a table shows each trial's counts, most work waiting first; clicking a row sets the trial in the One trial tab.
- Decision: counts use the latest match run of each trial only, so a trial's older runs never inflate the numbers. A review counts only while the model's answer is still the reviewed one (D85).
- Decision: a list draws at most 25 findings (each is a set of widgets); the rest are reached through the One trial tab.
- Check: Streamlit test runner: summary loads without errors, one accept moved Needs review 445 to 444 and Human-verified 0 to 1 (test review removed afterwards). Tests: 1,104 passed (one new, for the per-trial counts). Not tested by hand in a browser, and the table-row click was not simulated.


## D88. Cost estimate and confirm before Analyze (2026-11-13)

- Built: `estimate_match_calls` in `match_papers.py` works out the paid model calls a run would make (same papers, same reuse check, no call, nothing stored). Analyze Trial now downloads the trial and papers (free), asks the API which model version is served (about 20 tokens), counts the new calls, runs them at once if there are 5 or fewer, and otherwise shows "This will make N new model calls" with Run and Cancel buttons. The pending run is cleared before it starts, so a page redraw cannot run it twice. A stored trial also shows "at most N new calls" under the button (without the served-model check, so reuse can be overcounted and the real number is never higher).
- Result line now says new calls and reused answers separately.
- Check: tests prove the estimate equals the real first run (2 and 2) and 0 after it, respects the paper cap, and names why there is nothing to ask. Page: NCT00410124 (39 pairs at cap 3) showed the warning with Run 39 / Cancel and wrote no rows; Cancel cleared it. The Run button itself was not clicked (it would cost 39 calls). Tests: 1,108 passed.


## D89. Outcome overclaim audit and prompt fix (2026-11-13)

- Audit (`evals/audit_overclaims.py`, stored answers, no model call): 11 pairs where the model said identified and the labels said not reported (6 in the D42 run, 5 in the D84 check). None had an invented quote (the code check passes them all). Causes: 6 were bare statements with no number or test ("did not differ", "no other significant differences", "does not affect ... as measured by ..."); 3 were a subset, a split by a characteristic, or a different analysis set; 1 was a different time point; 1 was baseline values only. The main cause was a conflict: the prompt said a clear no-difference result counts as identified, while the rubric (line 91) labels a bare qualitative statement unclear.
- Fix: the prompt (`abstract_llm.py`) now treats these as unclear: a qualitative statement with no number, p-value or confidence interval; a general statement about several measures; baseline values only; a part of the participants; a different analysis set. A no-difference result still counts when it gives its numbers or test. The prompt hash changed, so stored answers are not reused for it. Schema unchanged.
- Dev rerun of 31 distinct trials (new prompt, no failures; one trial sits in both sets and is counted once, D91), same labels, same scoring, outcome level, 122 outcomes of which 48 are labelled reported: wrong identified 9 to 2 (D42 4 to 1, D84 5 to 1); correct identified 44 to 41 of 48 (D42 22 to 23, D84-only 22 to 18); precision 44/53 (83%) to 41/43 (95%), recall 92% to 85%. UNITS: this is outcome level. At pair level (one outcome in one paper) five pairs labelled yes became ambiguous and one became identified. At outcome level 4 reported outcomes in the D84 trials changed from identified to unclear and 1 in the D42 trials recovered (net 3 fewer). The 7 outcomes that are "yes -> unclear" now are the 4 new losses plus 3 that were already unclear before (D42 had 4 under the old prompt). "Unclear" is sent to review; that is routing, not recovered recall. It counts as recovered only when a reviewer then settles it correctly. The model's reasons for the new unclear answers follow the rubric (results only by age group or pregnancy status with no overall figure; qualitative only); several of those labels (mine) were looser than the rubric. Labels were NOT changed after seeing the result.
- Limits: DEVELOPMENT evidence only. The prompt was changed after seeing these overclaims, on the same pairs, so these numbers say nothing about unseen trials. A fresh unseen set, with the rule written first, is needed before any claim. Labels are one AI's, not human-verified. No further prompt tuning on these sets.
- Side effect: these 32 trials' latest match run is now the new-prompt run (the older runs are kept).


## D90. Dashboard exports (2026-11-13)

- Built: `trialtrace/review/export_findings.py` and Download CSV / Download report buttons in both tabs (one trial, or all trials). Each line has the model finding, the review (decision, reviewer, time, note, correction) and the versions (model, served model, prompt, schema, registry, paper file, run) in separate columns.
- Decision: `final_status` is filled only for Human-verified findings (the accepted model status, or the explicitly accepted correction). A pending correction, Unresolved and Needs review have none, so a reader of the file cannot mistake an AI answer for a settled one. The model's own answer is always kept in its own column.
- Decision: spreadsheet cells starting with = + - @ get a leading quote mark, so text from abstracts cannot run as a formula when opened in Excel.
- The exported list covers all three states of the chosen scope; it is not limited to the 25 findings drawn on the page.
- Check: 8 new tests (final status rules, formula protection, columns, database rows); the page shows four download buttons without errors; sample export of NCT03566667 read back. Tests: 1,116 passed. The files were not opened in Excel.


## D91. Correction: D84 was not entirely fresh (2026-11-13)

- Found while preparing the fresh validation set: NCT03566667 is the last trial of the D42 final set and also the first trial of the D84 cross-check (an off-by-one in "positions 80 to 150": the last D42 trial sits at position 80). D84 therefore had 12 new trials, not 13, and its rule line "never labelled or scored before" was wrong for one trial.
- Effect: D84 without the duplicate has 50 outcomes: recall 22/22, precision 22/27 (81%), same 5 overclaims. The pooled development figures in D89 count that trial once (122 outcomes). D84 was never pooled with D42, so no published headline number changed except the D84 figures, which are corrected in D84, README, the interview guide and the evals index.
- Also corrected: D89 had described "7 lost pairs". The figures were outcome-level; units are now separated in D89.
- The remaining positions of the saved draw (after the last D84 trial) hold no eligible trials, so the fresh validation set needs a new draw (D92).


## D92. Validation v1 prepared: fresh trials, rule and lock before labels (2026-11-13)

- Why: the D89 prompt fix was tuned on the same pairs it was measured on, so its numbers are development evidence only. Validation v1 asks whether the locked prompt judges correctly on trials never used.
- Locked first (`evals/validation_v1/system_lock.json`, `RULE.md`): model gpt-6.1-sol, prompt hash 4cde27b5b0b8, schema paper-match-v2, rubric with Addendum 2 (worked examples for bare "did not differ", subgroups, analysis populations, time points, baseline-only values; invented sentences, not from any trial). Pre-registered checks on the natural set at outcome level: precision of identified at least 90%, recall at least 80%, 0 bad quotes and 0 failed reads; share of unclear reported without a target.
- Draw (`evals/make_validation_draw.py`, seed 20261113): 150 trials from 2,806 eligible, excluding 9 used in earlier case files and 56 already in the database. Fetched without any model call: 150 registry versions, 56 trials with papers.
- Selection by the rule: natural set 19 trials, 204 pairs. The challenge set is empty: only 19 of 150 trials were eligible and the natural set used all of them. Not extended (a second draw would be a rule change made after seeing counts); reported as a limit, and hard cases are covered only as they occur naturally. A challenge set can be added later with its own draw.
- Human subset: 30 pairs chosen by SHA-256 order of seed + NCT + case ID (no label involved), blind sheet for Venu in `evals/validation_v1/venu_subset/`. Pairs Claude marks unclear are added later by the same order.
- Status: no label exists and no model has read these abstracts. Next: Claude drafts labels (recorded as AI-drafted), Venu labels the subset, labels are frozen by hash, then one run. `evals/validation_v1/LABEL_HISTORY.md` records what actually happens.


## D93. Validation v1 result: the D89 prompt on fresh trials (2026-11-13)

- One run of the locked system on the 19-trial natural set (71 outcomes, 204 pairs, 204 calls, no failures). Labels drafted by Claude before the run and frozen by hash; Venu's subset not yet labelled.
- Pre-registered checks, outcome level: precision 20/22 (91%, range 72-97%, target 90%); recall 20/25 (80%, range 61-91%, target 80%); 0 bad quotes, 0 failed reads; 30 of 71 outcomes called unclear (42%). Targets met only on point estimates and only just; the ranges are wide.
- On fresh trials the figures are lower than on the development pairs (95% and 85%), as expected after tuning.
- The 2 wrong identified and the 5 misses are partial matches to broad registered measures; the misses were all called unclear, so they reach review. No invented quote.
- Not shown yet: whether reviewers settle the unclear findings correctly (needs Venu's labels); no challenge set; one AI labeller. No prompt change follows from this run. Full write-up: [VALIDATION_V1_RESULTS.md](VALIDATION_V1_RESULTS.md).


## D94. The Venu subset: AI-prepared, reviewed and adopted by Venu (2026-11-13)

- The 50 blind pairs prepared for an independent human check came back filled in with a note written by an assistant. At first nobody had said Venu reviewed them, so they were recorded as a second AI labeller. Later the same day Venu stated that all 50 are reviewed by Venu. Recorded now (as in v6): prepared by an AI assistant, reviewed and adopted by Venu per Venu's statement; independence limited (reviewed after AI preparation, not blind-first). The label files are untouched; the record and file hashes are in `evals/validation_v1/venu_subset/VENU_REVIEW_RECORD.json`, the history in LABEL_HISTORY.md.
- Result: Venu-reviewed labels agree with the frozen AI-drafted labels on 40/50 pairs (80%, kappa about 0.63). The model matches the frozen labels on 37 of those 50 and the Venu-reviewed labels on 39. Headline scores unchanged.
- Main disagreement (6 of 10): whether a generic statement that does not name the registered measure is `unclear` or `no`. A rubric rule is needed for the next set; not applied to this one.
- Pre-registered checks (RULE.md): the human subset exists but was AI-prepared, so it is a review, not an independent first labelling. The check "how many unclear findings a reviewer settles correctly" was not done, because the reviewer did not see the model's answers. It stays open.


## D95. Dashboard: quote highlighted in the abstract, and review filters (2026-11-13)

- Each "identified" finding now shows the saved abstract with the source quote highlighted (`trialtrace/review/highlight.py`). The stored position is trusted only if the text there IS the quote; otherwise the quote is searched for in the abstract; if it is not found, nothing is highlighted and a warning tells the reviewer not to accept the finding without checking. Abstract text is escaped so formatting characters and dollar signs show literally.
- Check on the stored data: all 79 identified findings with a quote sit at their stored position (0 found by text only, 0 not found). The check is for the latest runs only.
- Filters above each list: model status, outcome role, and (all-trials tab) part of an NCT ID. The three state counts stay unfiltered; a caption says how many findings match. The CSV and Markdown exports still cover the whole tab, not the filtered view.
- Not built: a "risk" filter (the model status already separates identified, ambiguous and not identified), highlighting for not-identified or ambiguous findings (they have no quote), a side-by-side view of the registered outcome text.
- Tests: 6 new (position, fallback, missing quote, literal text, filters, quote position in the queue). Tests: 1,122 passed. Checked through Streamlit's test runner (11 highlighted abstracts on one trial, filter caption "79 of 649"), not by eye in a browser.


## D96. Assisted review of the model's unclear findings: tooling built, review not yet done (2026-11-13)

- Purpose: see whether the dashboard helps a reviewer reach a justified decision on the findings the model called unclear. Because the model's answer is visible, this is ASSISTED review, never an independent accuracy test; the dashboard, the instructions and the report all say so.
- Frozen before any review: `evals/validation_v1/assisted_review/selection.json` (56 pairs the model called unclear in the validation v1 run, ordered by SHA-256 of "assisted-review" + match id; hash in `selection.sha256`; 0 reviews existed). Instructions: `INSTRUCTIONS.md`.
- Recorded per finding (migration 026): decision, supporting evidence (sentence numbers or words), why it stays unclear (a fixed list of 9 reasons so they can be counted), a note, seconds on screen, plus the usual versions. "Unable to determine" now needs a reason from the list everywhere in the dashboard and API.
- Dashboard: an **Assisted review** tab shows one finding at a time in the frozen order: registered outcome with time frame and description, the model's named mismatch, the abstract with numbered sentences (the same numbering as the blind labelling sheets, tested), then four outcomes: identified, not identified, still unclear (evidence and reason required), unable (reason and note). Settling a finding saves a correction followed by its explicit acceptance, so it counts as Human-verified. Time is counted while the finding is on screen.
- Report: `python -m evals.report_assisted_review` gives how many were reviewed, the share settled, the share that stay unclear or undecided and the reasons, time per finding (median first, findings over 600 s flagged as likely pauses).
- Status: the 56 findings are waiting for Venu; no review row exists (the test rows made while checking were deleted). Tests: 11 new; the page was checked with Streamlit's test runner and in a real browser.

## D97. A real browser check found a launch bug (2026-11-13)

- Opened the dashboard in headless Chrome driven over its debugging port (screenshots in `outputs/browser_check/`). The first load showed `ModuleNotFoundError: No module named 'trialtrace'`: started the documented way (`streamlit run dashboard/app.py`) the page could not import the package, which Streamlit's own test runner and pytest cannot see. Fix: the page puts the project folder on the import path first; a test guards the order.
- After the fix, the All trials, One trial and Assisted review tabs render with no error on the page (649 findings, filters with the Model status help text, numbered abstract, the model's mismatch, the decision form).
- Limits: three tabs loaded and read as screenshots; not every button was clicked in the browser; one browser (Chrome), one window size, no phone layout.

## D98. Challenge set v1: rule written, draw made, labelling not started (2026-11-13)

- Why: validation v1 had few hard cases. This set is enriched on purpose for four failure modes (wrong trial attribution or pooled results, subgroup or partial results, different time points, bare "no difference"), scored on its own and never pooled with, or read as, natural prevalence. Rule: `evals/challenge_v1/RULE.md`, written before the draw, including a code-only screen (two of four string groups) and Addendum 3, which settles the validation-v1 rubric gap (a generic statement that does not name the measure is `no` unless it plausibly covers the measure).
- Draw (`evals/make_challenge_draw.py`, seed 20261114): 600 trials from 5,400 fresh eligible ones (45 used in earlier case files and 276 already stored were excluded). Registry and papers are fetched without any model call. Fetched: 600 registry versions, 235 trials with papers. Selection by the rule (`evals/select_challenge_v1.py`, 230 trials scanned in draw order): 10 trials, 151 pairs; of the 230, 198 were not eligible (too few papers or outcomes) and 22 were eligible but hit fewer than two string groups. Trials by group hit: attribution 6, subgroup 7, time points 5, bare no-difference 3 (most hit two or more), so bare no-difference is the thinnest. Labels, freeze and the one run are still to do; no label exists and no model has read these abstracts.

## D99. Phase 4 started: API, Docker, CI and a demo script (2026-11-13)

- API (`trialtrace/api.py`, FastAPI): `/health`, `/trials`, `/trials/{id}`, `/trials/{id}/findings.csv`, `/trials/{id}/estimate`, `/review-options`, and one write, `POST /findings/{id}/review` (a human review, same rules as the dashboard). It never calls the model and never downloads: analysing a trial costs money and stays a deliberate step in the CLI or dashboard. Errors: bad input 422 with the message, unknown trial or finding 404, anything unexpected logged with details and returned as a plain 500 with a request id and no details; every request is logged. Shared read-only lookups moved to `trialtrace/trial_view.py` so the dashboard and API use one copy.
- Checks: 11 tests (stored data, review apart from the model answer, rules as 422/404, hidden internal errors, free estimate); a live run against the real database returned the expected answers; a clean install of the package with the API and dashboard extras into a new virtual environment imports. Tests: 1,144 passed.
- Docker: `Dockerfile` (one image, non-root user, no secrets in it), `docker-compose.yml` (Postgres with a health check, API that migrates first, dashboard), `.dockerignore`. The compose file's syntax was validated; **the image was NOT built or run**: the Docker daemon was not running on this machine.
- CI: `.github/workflows/ci.yml` runs the tests against a Postgres service and checks that the image builds. **It has not run**: the project is not in a git repository here.
- Demo: `docs/DEMO.md`, an 8-minute script using stored trials, with the honest limits. Not recorded.
- Not done: authentication on the API (it is meant for a trusted network or localhost; the write endpoint has none), rate limits, a one-click deploy, a recorded demo.


## D100. Challenge set v1: draft labels written, human subset and reconciliation pending (2026-11-13)

- Order fixed by the owner: draft labels, then Venu's blind subset (completed before the model run, so its labels cannot be influenced by the answers), compare and reconcile from the source evidence, freeze the final labels, rubric, cases and system by hash, one run scored separately from natural validation. A string match only makes a trial a candidate: which failure modes actually occur is established by the labelling, and each label note carries the tags the labeller found.
- Done: 151 draft labels by Claude (AI-drafted, from the abstracts alone, before any model answer): pairs yes 12, unclear 37, no 102. Confirmed failure modes on the 37 unclear pairs: subgroup 24, wrong trial or pooled 13, bare statement 8, different time point 6 (they overlap). Venu's blind subset built (30 pairs by hash order + 20 draft-unclear pairs, no labels or tags shown) with `PROVENANCE.md` for the authorship record.
- Tooling (11 tests): `compare_challenge_v1` reports the ORIGINAL agreement and writes the disagreements with their source sentences; `reconciliation.json` holds one reasoned decision per disagreement; `freeze_challenge_v1` builds the final labels, keeps original and post-reconciliation agreement apart, checks the locked system is unchanged and that no model has read these trials, and hashes everything; `score_challenge_v1` reports overall results, results by confirmed failure mode, every wrong identified pair and every missed yes, and refuses to run before the freeze.
- Status: **blocked.** Venu's files came back, but part 1 is damaged (an earlier assistant message was pasted into it several times, one label line is cut off and one pair has no line; its hash no longer matches the provenance note) and the provenance note both calls the lines language-model-filled and calls them independent human labelling. The comparison script refuses to run on this, correctly. The model has not run. Files untouched.
- Limits that will be printed with the result: enriched on purpose (tests specific weaknesses; cannot establish general accuracy or prevalence), one AI draft labeller, a small human subset, 10 trials.


## D101. Challenge set v1: result (2026-11-13)

- Steps in the owner's order: 151 draft labels (Claude, AI-drafted), Venu's blind 50-pair subset (filled in by a language-model source, reviewed by Venu; part 1 was first damaged by pasted text and then restored, its original hash verified), comparison, reconciliation from the source text, freeze by hash (final labels yes 11, unclear 38, no 102; locked system verified unchanged; no model answer existed), one run.
- Original agreement before reconciliation: 46/50 (92%), kappa 0.85. Reconciliation of the 4 disagreements was done by Claude alone (3 labels changed); the agreement after reconciliation (49/50) is not independent and is kept apart. One known inconsistency (NCT00715273 m01 against m02) was left unchanged and is disclosed.
- Result, outcome level (43 outcomes): identified 11, labelled reported 9 (82%, range 52-95%); reported outcomes found with a valid quote 9/11; 2 wrong identified, both from one paper (a pooled-arms post hoc number at the right time point; the labeller had been unsure); 19 of 43 outcomes called unclear; 0 bad quotes, 0 failed reads. By confirmed failure mode: no wrong identified for wrong-trial or pooled (29 pairs), time point (6) or bare statements (8); 2 for subgroup or partial (26 pairs).
- Scored on its own, never pooled with validation_v1, and it cannot establish general accuracy. No change follows. Full write-up: [CHALLENGE_V1_RESULTS.md](CHALLENGE_V1_RESULTS.md).


## D102. Browser check of the review form; no default reason; Docker and Git still blocked (2026-11-13)

- Headless Chrome (driven over its debugging port, `outputs/browser_check/assisted_form.png`) rendered the Assisted review tab with no error on the page: the assisted-review warning, progress line, registered outcome with time frame and description, the model's named mismatch, the abstract with numbered sentences, the four decisions, evidence, reason, note, and a Save button that stays disabled until a decision is chosen. Nothing was saved (0 review rows afterwards). The reviewer-name box needs Enter to apply; the check typed it without confirming, which is a limit of the check, not a dashboard fault.
- Fix found by looking at the screenshot: the "why does it stay unclear" list started with "generic statement" already selected, so a reviewer who skipped it would record that reason by default and skew the counts the report is meant to give. Both reason lists (assisted tab and the normal queue) now start empty with a placeholder, and saving "still unclear" or "unable" without a reason is refused (tested in the page).
- Docker: the CLI is installed but Docker Desktop is not running, so the image has still not been built. Git: installed (2.52), but neither the project nor its parent folder is a repository, so CI has not run. Both need the owner.
- The 56 assisted-review findings are still unreviewed (0 reviews).


## D103. Assisted review done by an AI reviewer; AI reviews never count as human verification (2026-11-13)

- Asked who should review the 56 frozen unclear findings, the owner chose Claude as an AI reviewer. All 56 were reviewed as `Claude (AI)` with decision, evidence and reason (`evals/validation_v1/assisted_review/claude_ai_decisions.tsv`, `REVIEW_NOTES.md`), through the same save function the dashboard form calls. No review time was recorded: an AI reviewer's time is not a measure of human effort. This is ASSISTED review (the model's answer was visible), by the same AI that drafted the earlier labels: it is not independent and says nothing about how a person uses the form.
- Result (report: `python -m evals.report_assisted_review`, AI and human reviewers reported apart): 56 reviewed; 19 settled (4 identified, 15 not identified) = 34%; 37 stay unclear = 66%, 0 unable. Reasons the 37 stay unclear: generic statement 18, different time point 8, related but different measure 5, subgroup or part of a measure 4, predictor only 1, different analysis set 1. The 4 findings the reviewer settled as identified (the model had called them unclear) are re-hospitalisation length at 4-year follow-up (NCT00940394), urine 8-OHdG (NCT02058420 "oxidative stress marker"), serum delta-tocotrienol (NCT02058420), and urine NTX (NCT02058420 "bone resorption marker"): each is a named measure with a stated significant result in the right period but no numbers.
- Problem found while writing it up and fixed: the review state ignored who the reviewer was, so these 56 reviews would have appeared as Human-verified and used up the frozen list for a person. Now a reviewer whose name ends with (AI) is an AI reviewer: the review is kept, shown ("AI review by ... does not count as human verification") and exported in separate columns, but it never makes a finding Human-verified, never overrides a human review, and a correction can be accepted only by the same kind of reviewer. The Assisted review tab does not show the AI decision, so it cannot anchor a person. Dashboard after the change: Human-verified 0, assisted progress 0 of 56. Tests: 1,154 passed (5 new).
- Still open: a person's review of the same 56 findings (time per finding, whether the form helps); it can be done now in the Assisted review tab.


## D104. Dashboard polish, Google sign-in, Docker tested, deployed to Google Cloud (2026-11-15)

- Problem: the dashboard looked plain, showed stray `DeltaGenerator` text under the papers table (a `st.dataframe(...) if ... else st.write(...)` line that Streamlit printed), and anyone with the address could use it. Streamlit 1.45 in the Anaconda base environment also crashed on `st.button(width=...)`; the pin is now `streamlit>=1.51`.
- Choice: colored badges for model status and outcome role (colors mark the model's answer, never clinical risk), a teal theme in `.streamlit/config.toml`, a sidebar with review progress, colors and model. The "most papers" box was removed (the config value 10 is used).
- Login: `st.login()` with Google plus an email allowlist (`TRIALTRACE_ALLOWED_EMAILS`). It fails closed, the reviewer name is the signed-in email, and `TRIALTRACE_DISABLE_LOGIN=1` is for local use only. No user table. The dashboard talks to Postgres directly, so the API is optional and was not deployed or given authentication.
- Docker: images build, and compose started Postgres, the API and the dashboard (empty database). Deployed the dashboard to Cloud Run with Cloud SQL Postgres 16, secrets in Secret Manager and the stored data imported (800 findings, 68 trials). Real sign-in tested in a browser. Steps and fixes: [DEPLOY_GCP.md](DEPLOY_GCP.md).
- Trade-offs: the public URL relies on the sign-in and a two-email allowlist; the database costs about $10 to $15 a month; one small instance, not load tested; CI still has not run. A client secret was once pasted into the `.example` file and in chat; it was moved to the ignored file and reset.


## D105. Validation v2 prepared: larger, human-labelled, no AI draft (2026-11-16)

- Why: validation v1 met precision 90% and recall 80% only on the point estimates (20/22, 20/25; ranges 72-97%, 61-91%), with AI-drafted labels and a 50-pair human check.
- Rule written before the draw: `evals/validation_v2/RULE.md`. Same locked system as v1 (files re-checked, unchanged). Seed 20261116; 400 fresh trials drawn (never in a case file or the database), downloaded; 25 chosen by the same eligibility rule: 109 outcomes, 325 pairs. No challenge set.
- Labels: Venu labels every pair blind; a second person labels 25% (81 pairs) for agreement. No AI draft, no model answer shown. New check E: a target is "reliably met" only if the lower end of its range clears it (precision at least 85%, recall at least 75%).
- State: sheets and blank label files built; nothing labelled; no model call made. Next: labelling, freeze record, one run, scoring. See `evals/validation_v2/LABELLING.md`.
- Limits: 109 outcomes still gives wide ranges; one primary labeller; the 400 downloaded trials are now stored in the local database.

- Update 2026-11-16: the dashboard now also shows registry changes (One trial tab), results reporting by sponsor (Sponsors tab) and the fixed patterns (Patterns tab). Neutral colors, counts only, no sponsor score or ranking, each with the "stored trials only" note. These checks already existed in the report; only the display is new. Deployed as image v2.

## D106. Raw-source backup deployed to Google Cloud Storage (2026-11-19)

- The deployed bucket is `gs://trialtrace-514821-raw` in `US-CENTRAL1`. A read-only metadata check confirmed uniform bucket-level access and enforced public-access prevention.
- `save_raw` preserves the source locally, then attempts source and metadata uploads when `TRIALTRACE_RAW_BUCKET` is configured. Both registry and PubMed ingestion pass the setting. The optional `cloud` dependency and Application Default Credentials belong to the ingestion environment.
- Object keys mirror `raw/<source>/<NCT>/<hash>.<extension>` and the corresponding `.meta.json`. The upload adapter uses `if_generation_match=0` and treats an existing object as already retained. Source and metadata are separate uploads.
- An upload failure is logged while ingestion continues. Re-ingestion or an operator's create-only copy can reconcile missing objects. Local files, bucket writes, and PostgreSQL transactions have separate persistence boundaries.
- The hosted dashboard reads saved evidence from Cloud SQL. Its runtime does not read or write this bucket. Stored `raw_file_path` values remain local paths, and file consumers restore needed objects locally. Raw backup does not synchronize the local and cloud databases.
- Documentation, article diagrams, interview examples, and the video-demo deck in `docs/presentations/` now show the deployed backup path. Setup and recovery steps are in [DEPLOY_GCP.md](DEPLOY_GCP.md).
