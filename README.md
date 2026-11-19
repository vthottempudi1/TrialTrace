# TrialTrace: Clinical Trial Evidence Intelligence Platform

**Evidence-grounded review of clinical-trial reporting.** Live dashboard (Google sign-in, allowlisted emails): https://trialtrace-dashboard-1061960202985.us-central1.run.app. For each trial, TrialTrace compares what the registry says with
what the linked papers report, shows the exact evidence behind every statement, and leaves the decision to a person.

It is a prototype and a portfolio project, not a validated tool. Read the problem it addresses and its limits first:
[docs/PROBLEM_AND_POSITIONING.md](docs/PROBLEM_AND_POSITIONING.md).

## What it does

| Question | How it is answered | Status |
|---|---|---|
| 1. Has the trial posted results? | code, from the saved registry record | built |
| 2. How long since primary completion? | code | built |
| 3. Sponsor results record (this corpus only) | SQL counts, no stored score | built |
| 4. Patterns across trials | named SQL queries | built |
| 5a. Did a registered outcome change between registry versions? | code, from kept versions | built |
| 5b. Do the posted results match the registered outcomes? | code | built |
| 5b. Does a linked paper's abstract report a registered outcome? | rules baseline + LLM reader, quotes checked by code | built, evaluated on small sets |
| 6. Do the side effects match? | LLM reads the abstract's safety statements, code checks every quote and number and applies the comparison rules | built, **parked since 2026-11-13** ([D86](docs/DESIGN_DECISIONS.md)): reading is usable (v6 unseen run: recall 70%, precision 86%), targets not met, numeric and window comparison NOT validated |

Around those checks: a risk-ordered review queue (approve, edit, reject, or unclear, each with a reason), an audit
history that keeps the model's original answers apart from reviewer decisions, and reuse of stored model answers so the
same question is never paid for twice.

A Streamlit dashboard (`dashboard/app.py`) has five tabs: All trials, One trial (with a Registry changes panel), Assisted review, Sponsors (results reporting by sponsor, counts only) and Patterns (fixed questions over the stored trials). Details of the first three: enter an NCT ID, Analyze Trial, see outcomes, papers and findings, and review each finding (Accept, Correct, Unable to determine; the model's answer is never changed). It has an All trials tab (counts of Needs review, Human-verified and Unresolved across every trial, each opening its findings). Analyze Trial shows the number of paid model calls first and asks for a click above 5. Each finding shows its source quote highlighted in the abstract, and the lists can be filtered by model status, role and trial. Findings and reviews can be downloaded as CSV or a Markdown report. Also built: Google sign-in with an email allowlist, Docker files, and deployment to Google Cloud Run with Cloud SQL ([docs/DEPLOY_GCP.md](docs/DEPLOY_GCP.md)). A small API exists but is not deployed. CI has not run yet. Safety is parked (see above); the safety holdouts v2 to v6 have all been run and now count as development data.

## How it works

1. **Ingest** the registry record: the raw file is saved first, then parsed, with every value kept as citable evidence.
   Every version seen is kept, so a change can be shown between two dated versions.
2. **Find papers** for the trial in PubMed, save them, and check that each paper really names the trial.
3. **Read** each registered outcome against each paper's abstract with an LLM, told which trial (NCT ID) it is reading
   for. Code verifies every quote the LLM gives, where it sits in the abstract, and the paper type. A rules-only baseline
   is an optional second reader (`--with-baseline`), stored as separate rows. Safety statements are read the same way
   (`read-safety`) and compared with the registry's adverse-event table by plain code.
4. **Roll up** to one finding per registered outcome (and per safety quantity) across the papers read; when the baseline
   is on, disagreements between the two readers are marked.
5. **Review**: findings are provisional until a person decides. High-risk ones come first.

Rules the code enforces: differences are described for review and never as accusations (a test blocks verdict words);
unknown is never "no"; every claim cites evidence; nothing learns or changes itself. "Not identified" always means "not
identified in the available text (abstract)", never that a paper omits the outcome.

## Run it

Needs Python 3.12 and a PostgreSQL database.

```bash
pip install -e ".[dev]"
```

Put your settings in a `.env` file in this folder (never commit it):

```
TRIALTRACE_DATABASE_URL=postgresql://user:password@localhost:5432/trialtrace
OPENAI_API_KEY=...            # only needed for match-papers and the LLM evaluation
NCBI_API_KEY=...              # optional: allows more PubMed requests per second
```

```bash
python -m trialtrace.db.migrate                          # create or update the tables
python -m trialtrace ingest NCT04644237                  # download and store a trial
python -m trialtrace ingest-papers NCT04644237           # find and store its PubMed papers
python -m trialtrace match-papers NCT04644237            # LLM on every outcome and paper (calls the model API; --with-baseline adds the code reader)
python -m trialtrace read-safety NCT04644237             # LLM reads the safety statements in the abstracts (calls the model API)
python -m trialtrace report                              # build a report, saved as a run and as outputs/reports/<run>.md
python -m trialtrace review --reviewer "Your Name"       # decide on findings, highest priority first
streamlit run dashboard/app.py                           # the dashboard: enter an NCT ID, review findings
```

`match-papers` reuses a stored model answer whenever the same outcome, abstract, model and prompt were answered before
(`--refresh` asks again), and prints how many model calls it made. Tests need the database but never the network or an
API key, and run in their own `trialtrace_test` database:

```bash
python -m pytest
```

## API, Docker and the demo

```bash
pip install -e ".[api,dashboard]"
uvicorn trialtrace.api:app --port 8000      # docs at http://localhost:8000/docs
docker compose up --build                   # Postgres + API (8000) + dashboard (8501)
```

The API shows what is stored and takes human reviews; it never calls the model. The dashboard does not use the API: it talks to Postgres directly, so the API is optional. The Docker images build and `docker compose up --build` starts Postgres, the API and the dashboard (tested 2026-11-15, starting with an empty database). The CI workflow (`.github/workflows/ci.yml`) is written but has not run yet. A script for an 8-minute demo is in [docs/DEMO.md](docs/DEMO.md).

## Login and Google Cloud

The dashboard has Google sign-in with an email allowlist. Put `TRIALTRACE_ALLOWED_EMAILS=a@example.com,b@example.com` in `.env`, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in your Google OAuth client. With no allowed emails nobody gets in. `TRIALTRACE_DISABLE_LOGIN=1` turns login off, for your own computer only. Needs `pip install Authlib` (the `dashboard` extra has it).

The dashboard is deployed on Google Cloud Run with its database on Cloud SQL. Steps, problems and costs: [docs/DEPLOY_GCP.md](docs/DEPLOY_GCP.md).

## Evaluation

The LLM reader was compared with the rules baseline on frozen, labelled cases in three rounds, with labels fixed before
running and results reported even where the predefined rule was not met. Cases, rubrics, freeze records and run outputs are
in `evals/` (`rubric.md`, `v2/`, `v3/`, `v4/`, `runs/`). The numbers and their caveats are in
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) (D26b to D26r). The sets are small and the labels were drafted by a
model and reviewed by one person, so treat the results as a first experiment.

**Trial-with-several-papers evaluation (D42).** Final frozen run: 19 trials, 72 registered outcomes, 204 labelled pairs (labels drafted by a model; 30 pairs re-labelled blind by a person: 27/30 agree, kappa 0.85, the 3 misses corrected). At outcome level the LLM was right on 22 of its 26 "reported" claims (85%, 95% CI 66-94%) and found 22 of the 26 truly reported outcomes (85%); the baseline found 8 of 26 (31%). The LLM's quote was the right sentence 20/24 times (baseline 3/9). No invalid quotes or failed reads. All 4 LLM overclaims were sent to review (0 missed). Limits: abstracts only, 19 trials, 30 of 204 pairs human-checked; 20 of 34 "not identified" outcomes human-checked (0 missed).

**Safety evaluation (D56).** One frozen run on 53 trials / 154 abstracts (57 labelled statements; labels drafted by a
model, a blind second AI annotation reviewed by one person, adjudicated before the run): the LLM found 44 of 57 statements
(77%) and 44 of its 52 statements matched a label (85%); the comparison category was right for 27 of 57 (47%). No real
case of agreeing or differing numbers existed in the sample, so that part is untested; a separate set of 10 constructed
abstracts scored 17 of 18. This does not validate the safety comparison. Details: `evals/safety/report.md`.

**Safety rounds (D56 to D83).** Six rounds (v2 to v6) were run; none met its targets. The last, v6, ran once on unseen trials (123 abstracts, 54 trials, 61 labelled statements): the LLM found 43 (70%) and 43 of its 50 statements were right (86%); 0 verified numeric comparisons and 0 of 3 window assertions supported. Labels were prepared by a language model and reviewed and adopted by one person. Safety is parked ([D86](docs/DESIGN_DECISIONS.md), [SAFETY_V6_RESULTS.md](docs/SAFETY_V6_RESULTS.md)).

**Multi-paper cross-check (D84, 2026-11-13).** 12 new trials (a 13th was already in the D42 set, D91), one fresh run: nothing broke (0 failures, 0 bad quotes); at outcome level the LLM found 22 of 22 labelled-reported outcomes and 22 of its 27 claims were right (81%); all 5 overclaims went to review. Labels drafted by one AI and not human-verified, so this is a development check, not a validation. After a prompt fix aimed at those overclaims (D89), wrong identified outcomes fell from 9 to 2 on the development pairs and recall from 92% to 85%; a fresh unseen validation (19 trials, 71 outcomes, one run, D93) then gave precision 20/22 (91%) and recall 20/25 (80%) with 0 bad quotes and 42% of outcomes called unclear: the pre-registered targets (90% and 80%) are met only on the point estimates, with wide ranges, and the labels are AI-drafted (a 50-pair subset was reviewed and adopted by Venu and agrees on 40 of 50; the other 154 pairs are unreviewed) ([VALIDATION_V1_RESULTS.md](docs/VALIDATION_V1_RESULTS.md)).

**Challenge set (D101, 2026-11-13).** 10 trials chosen on purpose for four known failure modes (wrong-trial or pooled results, subgroups, different time points, bare no-difference), 43 outcomes, one run, scored on its own: 9 of 11 identified outcomes right; no wrong identified on wrong-trial, time-point or bare-statement pairs; 2 on subgroup pairs from one paper. It tests specific weaknesses and cannot show general accuracy; labels are AI-drafted with a 50-pair Venu-reviewed subset ([CHALLENGE_V1_RESULTS.md](docs/CHALLENGE_V1_RESULTS.md)).

**Assisted review of unclear findings (D103).** The 56 findings the model called unclear in the validation run were reviewed by an AI reviewer with the model's answer visible: 19 were settled (4 identified, 15 not identified) and 37 stay unclear (mostly generic statements and different time points). It is not a human review, not independent, and not an accuracy test; AI reviews never count as Human-verified in the dashboard, and a person's review of the same 56 is still open.

## Where to read more

Every document is grouped by topic in [docs/README.md](docs/README.md).

| File | What it holds |
|---|---|
| [docs/PROBLEM_AND_POSITIONING.md](docs/PROBLEM_AND_POSITIONING.md) | the documented problem, what this is, limits |
| [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) | a real trial followed through the pipeline |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | the data-flow diagrams, including deployment |
| [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) | the system design, for interviews |
| [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md) | every design decision, with the problem, the choice and the trade-off |
| [docs/INTERVIEW_GUIDE.md](docs/INTERVIEW_GUIDE.md) | the project story, honest limits and likely questions |
| [docs/SAFETY_PLAN.md](docs/SAFETY_PLAN.md) | the safety (side effects) work: plan, steps, rules |
| [docs/SAFETY_IMPROVEMENTS.md](docs/SAFETY_IMPROVEMENTS.md) | what changed after the first safety evaluation |
| [docs/LLM_IMPROVEMENT_ITERATION.md](docs/LLM_IMPROVEMENT_ITERATION.md) | the current state of the readers and the next evaluation |
| [docs/SAFETY_V6_RESULTS.md](docs/SAFETY_V6_RESULTS.md) | the last safety run and the audit of its misses |
| [docs/DEMO.md](docs/DEMO.md) | an 8-minute demo script |
| [docs/DEPLOY_GCP.md](docs/DEPLOY_GCP.md) | how the dashboard is deployed to Google Cloud, and what went wrong |
| [evals/README.md](evals/README.md) | every evaluation set, its status and how it may be used |
| [PLAN.md](PLAN.md) | the plan, the progress table and what comes next |

## Layout

```
dashboard/    Streamlit page (app.py)
trialtrace/   sources/ parse/ checks/ compare/ db/ review/ safety/ report.py ingest_*.py match_papers.py llm.py paper_reader.py cli.py
evals/        rubrics, labelled cases, frozen sets (v2 to v4, final, safety, safety_v2), run outputs, scoring and labelling tools
tests/        pytest suite
docs/         design, workflows, positioning, interview guide
```
