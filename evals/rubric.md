# Evaluation rubric (FROZEN 2026-11-05)

How we decide whether an LLM step is worth keeping. Everything here is fixed **before** any LLM code
is written, so the numbers cannot be bent to fit the result. Status: **frozen on 2026-11-05** (see the freeze block at the bottom). Nothing above the block changes.

## 1. What is being tested

Question 5b, paper arm: does a paper's **abstract** report a trial's registered outcome?

- **Case** = one registered outcome + one linked paper's abstract (a `listed` paper with an abstract).
- **15 cases**: 10 dev (used to build and tune) and 5 held out (used once, at the end).
- Split **by trial**, so the same trial is never on both sides. Dev: NCT01139775, NCT01295177, NCT02997202. Held out: NCT04644237, NCT03376932.
- Cases are chosen by rule (`evals/make_cases.py`), never by looking at any system's answer.

## 2. Labels (written by you, before any LLM exists)

For each case, from the abstract only:

| Label | Values |
|---|---|
| `reported` | `yes` / `no` / `unclear` |
| `reported_role` | `primary` / `secondary` / `not_stated` (only when `yes`) |
| `quote` | the sentence that reports it (only when `yes`), saved with its exact position |

Rules: "No" means "not in the abstract", never "not in the paper". `unclear` is a real answer.

**Where the labels came from (declared in every result):** the project owner labelled c01-c03 by reading. The other 12 labels, and the `yes` on c02, were adopted from a label table suggested to the owner (source not an independent expert). Each label records this in its `annotator` field, with the table's reason in `notes`. This is not independent expert labelling, so results are reported as "owner-adopted, single-source labels" and are never blended with outside labels. If a model from the same family as the suggester is evaluated later, the labels may favour it; this is stated with the result.

## 3. Systems compared

1. **Baseline**: `compare/abstract_baseline.py` (code only, already built).
2. **LLM version**: built later (lessons 9-12).

Same frozen cases, same abstracts, same labels for both.

## 4. Metrics

| # | Metric | How it is computed |
|---|---|---|
| M1 | **Identification accuracy** (primary metric) | on cases labelled `yes` or `no`: the system says `identified` when `yes`, `not_identified` when `no`. `ambiguous` or no answer counts as not correct |
| M2 | Role accuracy | on `yes` cases: the system's role equals the labelled role (`not_stated` equals no cue). Abstaining is not correct |
| M3 | **Bad quotes** | any `identified` whose quote is not at its stated place in the abstract, or does not contain the name the system claims to have found. Must be **0**. This means the quote is real and names the outcome; it does **not** prove the sentence reports the outcome's result |
| M3b | Quote agreement (reported, not in the keep rule) | on `yes` cases: the system is `identified` and its quote is the very sentence the labeller picked as the one that reports the outcome |
| M4 | False "identified" | system says `identified`, label says `no` |
| M5 | Abstention quality | on `unclear` cases, the system should say `ambiguous` or `unclear`. Reported, never rewarded on `yes`/`no` cases |
| M6 | Failures | extraction failures, shown as a count, never dropped |
| M7 | Cost | latency and tokens per case (LLM only) |
| M8 | Human review time and check | time to check 5 findings with the LLM version versus the baseline, timed. In the same pass a person reads each `identified` quote and says whether it reports the outcome (the judgment M3 cannot make) |

Every metric is reported **per split** (dev and held out) and never mixed.

## 5. The rule for keeping the LLM version

The baseline's dev scores are known and fixed (saved in `evals/runs/`): **M1 = 5 of 7, M2 = 0 of 3, M3 = 0, M3b = 0 of 3, M4 = 0**.

The LLM version is kept only if, on the **dev** cases, **all** of these hold:

1. M1 is at least **1 case higher** than the baseline's: **6 of 7 or better**.
2. M2 is at least **1 case higher** than the baseline's: **1 of 3 or better**. This is where an LLM should add something, since the baseline gets none.
3. M3 is **0** (bad quotes).
4. M4 is **0** (the baseline's value): no false "identified".

Then the held-out cases are run **once** and reported, whether the result is good or bad. If the rule fails, we ship the baseline and write up the negative result. That is a valid outcome.

## 6. Honest limits (printed with every result)

- 15 cases is a first experiment, not validation. On 10 dev cases, one case is 10 points. No claim of statistical significance.
- One label source (the owner, adopting a suggested table). No independent check.
- Abstract only. A "no" says nothing about the full text.
- 5 trials, all in our own corpus.
- Any later tuning after the held-out run needs a **new** held-out set.

## 7. What gets saved for every run

Full model outputs, prompts, model name and settings, the validation result of every quote, and every failure.

## Change log (before the freeze)

- The first draft said M1 "at least 2 cases higher" and M2 "not lower". The baseline then scored M1 = 5 of 7, so "+2" would have needed a perfect 7 of 7, and "M2 not lower" is met by any system when the baseline scores 0. The rule was changed **after** seeing the baseline's dev score but **before any LLM code existed**, so it could not be shaped by an LLM result. The held-out cases were not looked at.

- M3 was renamed from "unsupported claims" to "bad quotes", because it only proves the quote is real and names the outcome. On the dev run the baseline gave a real but wrong sentence for c09 (a background sentence naming FLT3-ITD instead of the result sentence). Added M3b (quote agreement with the labelled sentence) as a reported metric, and a person's read of each `identified` quote inside M8. The keep rule did not change. Done before any LLM code existed; held-out cases still unseen.

- Before the freeze, the owner delegated a review of the labels. One changed: c13 (held out) from `yes` to `unclear`, because the registered outcome is all treatment-emergent adverse events while the abstract reports only grade 3 or higher drug-related ones, a narrower set. The old label is kept in its history. The other judgment calls (c03, c09) were kept. No held-out case had been scored. The unused `role_cue` function was deleted from the baseline (no behaviour change; 506 tests pass; dev scores unchanged), then the files were hashed.

## Addendum for the multi-paper pilot (2026-11-10; the v1 freeze above is unchanged)

Labelling rules the owner applied, in this order of appearance, to the 7 trials of `evals/multipaper/` (single annotator, blind to system answers). They were adopted while labelling and are written down here before the totals are read:
- A result for a narrower or different set than the registered measure (c13; m07: grade 3 or higher events for "any adverse event") is `unclear`.
- A result pooled over several trials, or reported only for a subgroup or a different time point than the registered one, is `unclear` (NCT00353938, NCT00770315, NCT02606422).
- A measure used only as a predictor or inside an association analysis, with no result of its own, is `unclear` (NCT04061915).
- A qualitative statement with no number or test ("no difference between arms") is `unclear`, even when it names the measure; a direction with a test (p-value) or a numeric value is `yes` (NCT01060540). This line decides 3 of the model's 4 overclaims on that trial.
- A result from a pilot run described as separate from the registered trial is `unclear` (NCT04550221).
- A different instrument, intervention or population (for example an infographic versus a cardiovascular environment) is `no`.
Role: `not_stated` unless the abstract uses the word primary or secondary for that measure.

| Item | Value |
|---|---|
| Frozen on | 2026-11-05 |
| `evals/cases.jsonl` SHA-256 | 0fdee1e915cb682f2cab2ab88e99069e50755d431a3fd7c2cd4b0ae3fe892dcc |
| Baseline source SHA-256 (`trialtrace/compare/abstract_baseline.py`) | 1710728a6a9efda9d4fe5004cf98fc19f9f0093f3ac52a10e0004bfa49c48df5 |
| Rule in section 5 changed since freeze? | no |


## Addendum 2 for validation v1 (2026-11-13, written before any validation trial was drawn)

Worked examples for the five judgement calls the overclaim audit (D89) found. They are invented sentences, not taken from any trial. The rules are the same as in the addendum above; this only makes them concrete. Answers: yes / no / unclear.

1. **Bare "did not differ".** Registered: number of participants hospitalised. "The groups did not differ in hospitalisations." -> `unclear` (qualitative, no number, test or interval). "Hospitalisation was 12% vs 14% (p = 0.6)." -> `yes`. "No significant difference was found for the other tests." about a different measure -> `no` for the registered measure unless it is named.
2. **Subgroup or part of the participants.** Registered: overall response rate. "Response was 40% in women and 30% in men." with no overall figure -> `unclear`. "Overall response was 35% (95% CI 28-42)." -> `yes`, even if subgroup figures follow.
3. **Analysis population.** Registered: the randomised comparison of A vs B. "Among all patients who received A (including the extension), CD4 rose by 17." -> `unclear`. The intention-to-treat or per-protocol result of A vs B -> `yes`.
4. **Time point.** Registered: change at 12 months. A result only "at 3 months" -> `unclear`. "At 12 months, mean change was -4 (p = 0.01)." -> `yes`. A result "during follow-up" with a number and no stated time -> `yes`.
5. **Baseline values only.** Registered: change in glucose from baseline. "Baseline glucose ranged from 66 to 179 mg/dL." -> `no` (no result for the outcome). "Glucose fell from 120 to 104 (p < 0.01)." -> `yes`.
6. **A number with a test for the right measure, time and population** -> `yes`. A direction with a p-value counts. Role stays `not_stated` unless the abstract says primary or secondary for that measure.
