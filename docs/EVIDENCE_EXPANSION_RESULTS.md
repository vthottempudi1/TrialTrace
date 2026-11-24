# Evidence expansion (V2 agent test): results and verdict

Date: 2026-11-24. Question: does an agent that reads more sources reduce ambiguous and not-identified outcomes?

**Scope:** "more sources" here means abstracts plus the body paragraphs of open-access full text (top 5 keyword-matching paragraphs per paper). **Tables, figures and supplements were not read**, and the "possible, not verifiable" status was not built. Results reported only in tables are counted as not found, so the coverage numbers below are probably too pessimistic.

**Evidence-expansion verdict (2026-11-24): neither the fixed expansion baseline nor the agent showed enough practical benefit to justify shipping as part of V1.** On 25 outcomes, V1 had 17 correct final statuses, the expansion baseline 18, and the agent 19. The agent's one-case advantage over the baseline does not establish a meaningful improvement; it used 350 tool/model calls versus 346. Both expansion approaches made one primary false upgrade, missing the target of zero.

The strict model-drafted labels classified 27 of 40 unresolved outcomes as not identified in the readable sources; only 165 of 344 fetched papers had open-access full text. This points to source coverage as the main limit in this experiment. Keep V1 as the core and prioritize wording that states exactly which sources were searched when reporting an abstention. Keep expansion code and guards as experimental work. These results do not prove either approach useless in general: the sample is small, labels had no human review, each configuration ran once, and run 2 followed fixes informed by locked-test errors.

## What was built
`trialtrace/evidence_expansion/`: `baseline/` (fixed expansion: full text of linked papers, companion papers by NCT and title search), `agent/` (cause question, ladder, new search words, hard budget of 15 calls), `eval/` (cases, gold, metrics, injection test). 20 tests in `tests/`.

## Test set
40 unresolved outcomes (20 primary, 20 secondary) from V1: 15 dev, 25 locked test. Plus 6 control outcomes V1 already marked identified.
Gold labels were drafted by a stronger model that saw every readable source, with strict rules (a result needs numbers or a test) and a code check on each quote. **No human review.**

## What the gold says (40 outcomes)
27 not identified, 10 ambiguous, 3 identified. Under these model-drafted labels, most unresolved outcomes had no identified result in the sources collected and legally readable in this experiment. This does not establish that no result exists elsewhere. Only 165 of 344 papers fetched had open-access full text.

## Locked test (25 outcomes), run 2 (after two code guards)
| | V1 | Baseline | Agent |
|---|---|---|---|
| Correct final status | 17/25 | 18/25 | 19/25 |
| Resolved (V1 wrong, now right) | 0/8 | 5/8 | 4/8 |
| False upgrades (primary / secondary) | 0 / 0 | 1 / 1 | 1 / 0 |
| Controls changed wrongly | 0/6 | 0/6 | 0/6 |
| Tool+model calls | 0 | 346 | 350 |
| Tokens | 0 | 94,649 | 81,661 |

Run 1 (before the guards): baseline 15/25 with 4 false upgrades, agent 17/25 with 4 false upgrades. Quote validity was 100% in both runs.

## Three problems and guards found along the way
1. Papers found by free-word search were often from other trials and caused false upgrades. A free-text paper now counts only if its PubMed record lists the trial's NCT ID (55 of 588 did).
2. Method sentences ("the primary outcome was X") were read as results. An identified quote must now contain a number, or it becomes ambiguous.
3. Injection test: one poisoned passage got through because the injected text held the quote. A phrase guard now drops such passages. 0 of 3 obeyed.

## Verdict (for abstracts plus body paragraphs only)
**Scope:** only body paragraphs of open-access papers were read. Tables, figures and supplements were NOT read, so this is not a test of full-text reading. Many secondary outcomes appear only in tables, so the real gain from full text may be larger than measured. Table extraction is the one untested step.

- **Do not ship either expansion approach as part of V1 on this evidence.** Neither showed enough practical benefit. Agent and baseline differ by one case out of 25, which is too little to establish superiority. The agent used more calls (350 vs 346) and about 14% fewer recorded tokens; its cause and query-word calls are excluded from those token counts.
- **The gain from reading more sources is small** (+1 to +2 correct of 25), and the primary false-upgrade target of zero was not met (1 in each).
- **The main finding is coverage.** Most unresolved outcomes were not resolved in the readable sources collected for this experiment. Per the plan, this is the "mostly C and D" branch: improve how V1 reports abstention instead.
- Retain the experimental baseline, agent and guards for future research; do not enable expansion by default or present it as a validated optional improvement. Both preserve V1 identified statuses, but that safeguard did not prevent false upgrades on unresolved outcomes.
- This expansion baseline is a fixed source-retrieval workflow using model calls. It is separate from the earlier keyword/code-only abstract baseline; this verdict does not overturn that earlier comparison.

## Limits (say these out loud)
Gold is model-drafted from the same sources the systems read, so it favours them. 25 test cases is too few to separate 18 from 19. The locked set was used to find the two guards, so run 2 is not a clean test. One run each. Agent cause and query-word calls are not in the token counts. Full-text coverage is under half. Tables and figures were not read.

## Table audit (2026-11-24, follow-up)
Question: did skipping tables hide results for the 27 outcomes gold marked "not identified"?
Method: `baseline/get_fulltext.py::xml_to_tables` keeps each table's caption, headers, rows and footnote; `eval/table_audit.py` searched every open-access table of each trial's linked and NCT-found papers for the outcome's words, then the matching tables were read.
- 21 of 27 outcomes had at least one open-access paper with a body; 6 had none (nothing to audit).
- 8 outcomes had matching tables. **None reported the registered outcome's result.** They were baseline-characteristics tables, covariate tables, tables from other studies, or a Cochrane review's summary tables.
- 2 papers (the DESTINY-Lung02 primary paper, for t07 and t19) hold 4 tables saved as pictures. These cannot be read and could hold results.
- Not checked: figures, supplements, and the 6 outcomes without open-access text.
Result: on this sample, tables did not change the gold or the verdict. The verdict still covers only open-access text; picture tables, figures and supplements remain unread.
