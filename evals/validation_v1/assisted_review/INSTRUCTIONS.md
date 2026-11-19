# Assisted review of the model's unclear findings: instructions (written 2026-11-13, before any review)

**What this is.** You review the findings the model called *unclear* in the validation v1 run: 56 outcome-and-paper pairs, frozen in `selection.json` (hash in `selection.sha256`) in a fixed order. You can see the model's answer and the mismatch it named. So this is an **assisted review**: it shows whether the dashboard helps a reviewer reach a justified decision. It is **not** an independent accuracy test and must not be reported as one.

**How.** Start the dashboard (`streamlit run dashboard/app.py`), enter your reviewer name in the sidebar, open the **Assisted review** tab. One finding is shown at a time, in the frozen order; the next appears after you save. Work through them in one sitting if you can: the time a finding is on screen is recorded, and a long pause inflates it.

**For each finding choose one outcome:**
1. **Identified**: the abstract reports a result for this registered outcome (same measure, population and time frame, with a number or a test). Give the evidence.
2. **Not identified**: the abstract does not report a result for this outcome. Give the evidence (what it reports instead, or that it does not mention it).
3. **Still unclear**: you agree the abstract cannot settle it. Give the evidence and pick the reason it stays unclear.
4. **Unable to decide**: you cannot tell. Pick a reason and write a note.

**Evidence** is the sentence number(s) shown in the abstract, or the words you relied on. Required for the first three outcomes.

**Reasons a finding stays unclear** (fixed list so they can be counted): generic statement; only a subgroup or part of a measure; a different time point; a different analysis set or comparison; the result may belong to another trial or is pooled; a related but different measure; the measure is used only as a predictor; the abstract is too short; other (explain in the note).

**Rules to apply:** `evals/rubric.md` including Addendum 2 (worked examples). Use the abstract only; "not identified" means not in the abstract. Do not look at the frozen labels, your earlier 50 blind labels or any other model run while reviewing. A decision is final once saved; if you change your mind, the report uses the last decision for each finding and counts the change.

**What gets recorded for each finding:** your name, decision, evidence, reason, note, time on screen, and the exact model, prompt, schema and source versions.

**What gets reported** (`python -m evals.report_assisted_review`): how many findings were reviewed; the share settled as identified or not identified; the share that stay unclear or undecided and the reasons; the time on screen per finding (median first, with pauses flagged); and that the review was assisted.

**Limits to state with the result:** one reviewer; the model's answer was visible; abstracts only; the 56 findings come from one run on 19 trials and are not a sample of all unclear findings.
