# Assisted review by an AI reviewer (2026-11-13)

**Who.** The 56 frozen findings were reviewed by Claude (an AI), recorded under the name `Claude (AI)`. The owner chose this when asked who should review. It is **not** a human review: no person has reviewed these findings, and no review time was measured (an AI reviewer's time says nothing about human effort). A person can still review the same 56 findings in the Assisted review tab; the dashboard counts only human reviews towards Human-verified and the progress line, and the report keeps AI and human reviewers apart.

**How.** Each finding was read as the form shows it (registered outcome, time frame, description, the model's named mismatch, the abstract with numbered sentences) and decided with evidence and, where it stays unclear, a reason from the fixed list. The decisions were saved with the same function the form calls (`save_assisted_review`), not by clicking through the form 56 times; the form itself was checked separately in a browser and with tests. The full list with rationale: `claude_ai_decisions.tsv`.

**Principle applied throughout (so the generic-statement calls are consistent):** if the abstract makes a statement whose subject plausibly includes the registered measure AND gives a direction about that subject ("improved", "no effect", "no unexpected safety issues"), the finding stays unclear as a generic statement. If the abstract never speaks about that subject, or reports a clearly different measure, sample or comparison that a reader can exclude, it is not identified. A named measure with a stated significant direction and the right period counts as identified.

**Limits to state with any use of this review.**
- The reviewer is the same AI that drafted the earlier labels, saw the model's answer, and is not independent of either. It does not measure whether a person finds the form helpful or how long a person takes.
- The generic-statement calls (the "clinical and radiographic parameters improved" statements in NCT03564301 and the "no unexpected safety issues" statements in NCT04193189) are the most arguable: the Venu-reviewed labels in validation v1 called several of the same pairs `no`.
- 56 findings from one run on 19 trials.
