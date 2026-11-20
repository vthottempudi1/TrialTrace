# TrialTrace: Clinical Trial Evidence Intelligence Platform. Workflows

How data moves through TrialTrace: the registry side, the PubMed side, safety, and
the final combined picture. For the reasons behind each step see `DESIGN_DECISIONS.md`;
for one trial followed end to end see `HOW_IT_WORKS.md`.

## Current v1 storage and review workflow (2026-11-19)

```mermaid
flowchart LR
  CT["ClinicalTrials.gov"] --> ING["Configured local ingestion"]
  PM["PubMed"] --> ING
  ING --> LOCAL[("Local raw files and metadata")]
  ING -->|"Create-only backup uploads"| GCS[("Cloud Storage<br/>trialtrace-514821-raw")]
  ING --> DB[("PostgreSQL<br/>Parsed records and evidence")]
  DB --> CHECK["Registry checks and outcome-abstract matching"]
  CHECK <--> LLM["Model reader"]
  CHECK --> VALID["Quote validation and findings"]
  VALID --> DB
  DB --> UI["Dashboard and human review"]
  UI --> DB
```

Ingestion writes locally before attempting the cloud backup. Registry and PubMed sources use the same `save_raw` adapter. The bucket stores source files and `.meta.json` at `raw/<source>/<NCT>/<hash>.<extension>`. Upload failures are logged per object while ingestion continues. Re-ingestion or an operator's create-only copy can reconcile missing objects.

The current hosted dashboard uses Cloud SQL for saved records and evidence. It has no runtime bucket read/write path. Local-file consumers can restore their source paths from the backup objects. Raw backup and local-to-cloud database synchronization are separate operations. Setup is in [DEPLOY_GCP.md](DEPLOY_GCP.md#raw-source-files-in-cloud-storage-added-2026-11-19).

## Historical combined workflow (2026-11-11)

```mermaid
flowchart LR
  R[Structured registry data] --> C[Code checks and arithmetic]
  A[Abstract plus target NCT ID] --> L[LLM extraction]
  L --> V[Quote and field validation]
  V --> C
  C --> U[Specific unresolved reason]
  U --> H[LLM match and window proposal]
  H --> P[Source checks and human decision]
  P --> C
  C --> Q[Evidence-backed provisional report and review]
```

The code baseline is an optional diagnostic (`--with-baseline`) for both readers, not a prerequisite. A proposed
name match cannot override population, definition, denominator or collection-window requirements. Reports do not
make model calls. The outcome reader identifies the target trial explicitly and cache keys include that ID.

The unseen safety v2 draw (30 trials / 127 abstracts (123 strict unseen)) has labels for its first 30 trials (drafted by AI, reviewed by the owner, 2026-11-11) and 10 added trials still to label; nothing is frozen and no model has been run
on it. The registry wording audit found no candidate wording pairs, so no such helper is built. Historical workflow
milestones below retain their old evaluation context; current details are in `LLM_IMPROVEMENT_ITERATION.md`.

**Colours in every diagram**

| Colour | Meaning |
|---|---|
| Green | Built and tested |
| Amber | Next to build |
| Grey | Planned, later |
| Cylinder | Something saved (database table or file) |

**Three rules behind every arrow**

1. Unknown is unknown: a failure is recorded, never turned into "no".
2. Every claim cites evidence; a claim with no evidence is refused.
3. Differences are "for review", never a verdict.

---

## 1. Registry workflow (built)

### 1A. Getting one trial in (`python -m trialtrace ingest NCT...`)

```mermaid
flowchart TD
  A["ClinicalTrials.gov<br/>one trial by NCT ID"] --> B["fetch with retries<br/>3 tries, only temporary errors"]
  B -->|"404 not found"| F[("ingest_failures<br/>step, error, tries")]
  B -->|"still failing"| F
  B -->|"ok"| C[("raw file<br/>untouched, named by its hash")]
  C -->|"Configured backup"| GC[("Cloud Storage<br/>raw source and metadata")]
  C --> D["parse the record<br/>snapshot + evidence"]
  D -->|"unreadable or wrong trial"| F
  D --> E["parse the outcomes<br/>primary, secondary, other"]
  E -->|"bad outcomes section"| F
  D --> E2["parse the reported outcomes<br/>posted results only"]
  E2 -->|"bad results section"| F
  E --> G["save in ONE transaction"]
  E2 --> G
  D --> G
  G --> S1[("snapshots<br/>each distinct version once")]
  G --> S2[("observations<br/>every time we looked")]
  G --> S3[("evidence<br/>exact value + where it came from")]
  G --> S4[("registered_outcomes<br/>one row per outcome")]
  G --> S5[("results_outcomes<br/>one row per reported outcome")]

  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  class A,B,C,D,E,E2,G,F,S1,S2,S3,S4,S5 built
```

Key points
- The raw file is saved **before** parsing, so a parser bug never loses the original.
- A bad outcomes or results section is recorded, but the snapshot is still stored, with that part marked unknown.
- The same content seen again adds an observation, not a new snapshot (so `A -> B -> A` is kept in order).

### 1B. Answering questions 1 to 5a, then report and review

```mermaid
flowchart TD
  DB[("snapshots + evidence<br/>observations + outcomes")] --> V["latest version of each trial"]
  V --> Q1["Q1 results posted?<br/>results_posted"]
  V --> Q2["Q2 how long since completion?<br/>timeline"]
  V --> Q3["Q3 results per sponsor<br/>track_record, SQL"]
  V --> Q4["Q4 patterns across trials<br/>3 named SQL queries"]
  DB --> R2["two most recently seen<br/>different versions"]
  R2 --> Q5["Q5a did registered outcomes change?<br/>compare_outcomes"]
  Q1 --> FD["Findings<br/>claim + evidence IDs + limitations"]
  Q2 --> FD
  Q5 --> FD
  FD --> RP["report<br/>python -m trialtrace report"]
  Q3 --> RP
  Q4 --> RP
  RP --> RUN[("runs + findings<br/>each report is a saved run")]
  RUN --> RV["review<br/>approve, edit or reject"]
  RV --> HIST[("reviews<br/>insert-only history")]

  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  class DB,V,Q1,Q2,Q3,Q4,R2,Q5,FD,RP,RUN,RV,HIST built
```

Key points
- Questions 3 and 4 are labelled "in the ingested corpus only".
- 5a never says "no change" when it has only one version or no outcomes: that is "unknown".
- A removed outcome next to an added one in the same role goes to **needs review**, not to "changed".
- By default review asks only about differences and items needing review (`--all` adds plain facts).

### 1C. Registered vs reported outcomes, results arm (built, in the report)

```mermaid
flowchart TD
  RO[("registered_outcomes<br/>the plan")] --> M["match by title<br/>capitals and punctuation ignored"]
  RS[("results_outcomes<br/>what was reported")] --> M
  M --> D1["same title:<br/>compare role and time frame"]
  M --> D2["on one side only:<br/>registered_only / reported_only"]
  M --> D3["duplicates, unreadable type,<br/>or a missing + extra pair in one role"]
  D1 --> DF["differences"]
  D2 --> DF
  D3 --> NR["needs review<br/>never called a difference"]
  RS -->|"no results posted"| UN["unknown / no results posted<br/>never 'no differences'"]
  DF --> FD["Finding<br/>comparison_arm = results_submission<br/>match_method = deterministic"]
  NR --> FD
  UN --> FD

  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  classDef next fill:#fef3c7,stroke:#d97706,color:#78350f
  class RO,RS,M,D1,D2,D3,DF,NR,UN,FD built
```

Key points
- Both sides are structured registry records, so no LLM is needed.
- Descriptions are not compared (results descriptions are usually longer).
- The registered list is from the same saved version, so this is not a comparison with the original plan.
- The finding is saved with its `comparison_arm` and `match_method`, shown in the report, and a match is never queued for review.

---

## 2. PubMed workflow

### 2A. Getting papers in (built: `python -m trialtrace ingest-papers`)

```mermaid
flowchart TD
  N["NCT ID from a stored trial"] --> S1["search PubMed<br/>candidate paper IDs"]
  S1 -->|"unavailable or invalid"| F[("failure recorded<br/>never read as no papers")]
  S1 -->|"ok or not_found"| S2["fetch the paper records<br/>XML, up to ~20 papers per file"]
  S2 --> RAW[("raw XML file<br/>untouched, named by its hash")]
  RAW -->|"Configured backup"| GC[("Cloud Storage<br/>raw source and metadata")]
  RAW --> P["parse into one Paper per PMID<br/>title, abstract, type, listed trial IDs"]
  P --> ST[("papers")]
  P --> LK["link check, code rules<br/>is this paper really about this trial?"]
  LK --> LNK[("trial-paper links<br/>candidate or confirmed, with the reason")]
  S1 -.-> SR[("paper_searches<br/>query, count, status, batches_failed")]

  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  classDef next fill:#fef3c7,stroke:#d97706,color:#78350f
  classDef planned fill:#e5e7eb,stroke:#6b7280,color:#374151
  class N,S1,S2,RAW,P,ST,LK,LNK,F,SR built
```

Key points
- A search hit is only a **candidate**. The link check grades it: `listed` (the paper's ID list names the trial), `text_only` (ID only in the title or abstract) or `not_confirmed`. All are stored with the reason.
- Request pacing: about 3 requests a second without a key, so we pause 0.4 s before each request and retry on 429.
- Every search is recorded. A search can be `ok` yet partial if a batch failed, so check `answered_fully`.
- One XML file holds many papers, so evidence and links are identified by **PMID** plus the file hash.
- We read the **abstract** only, so results will say "not identified in the available text (abstract)".

### 2B. Does the paper report the registered outcomes? (question 5b)

```mermaid
flowchart TD
  RO[("registered outcomes<br/>from the registry side")] --> BASE
  AB[("confirmed paper<br/>abstract text")] -. optional .-> BASE["code-only baseline<br/>normalised names, known abbreviations"]
  AB --> EX["LLM extraction<br/>reported outcomes, each field with a quote"]
  EX --> VAL["code check<br/>every quote must appear in the abstract"]
  VAL -->|"fails"| XF[("extraction failure<br/>recorded, not dropped")]
  VAL --> MT["matching<br/>code first; LLM only for pairs code cannot decide"]
  RO --> MT
  MT --> CMP["field rules<br/>measure, timepoint, population, role"]
  BASE --> CMP
  CMP --> FD["Findings for review<br/>supported differences, unknowns, or 'not identified in the abstract'"]
  FD --> EV["evaluation<br/>absolute quality, coverage and human review requirements"]

  classDef planned fill:#e5e7eb,stroke:#6b7280,color:#374151
  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  class RO,AB,BASE,EX,VAL,XF,MT,CMP,FD,EV built
```

Key points
- The LLM only **reads text** and answers small questions. Code checks the quotes and applies the comparison rules.
- Ambiguous or multiple matches always go to review; the first item is never picked silently.
- Current quality targets are absolute and frozen before evaluation: extraction quality, wrong matches, false reassurance, useful comparison coverage and review routing. Baseline superiority is not a release gate.

**Built and measured (D37-D42).** Per outcome, across all papers read: any paper identified -> identified; else any unclear -> unclear; else unread/failed -> unknown; else not identified. When the optional baseline is enabled, both readers are rolled up and a difference is marked disputed. An absent baseline creates no disagreement. Risky findings are queued first: unclear, disputed, identified in different papers, or identified with no number or test beside the outcome name. A reviewer can also label each link (reports results / protocol / mentions only / not this trial). Final frozen result on 19 trials: LLM found 85% of reported outcomes, baseline 31%; all overclaims reached review.

---

## 3. Safety workflow (question 6: built, evaluated six times, not validated, PARKED since 2026-11-13, D86)

Same shape as 5b, with a safety contract. Built so far: the registry side (parse, store) and a registry-only finding per trial (deaths, serious events, top serious events per group, with evidence; `checks/safety_registry.py`). On the paper side the code baseline reader is built (`compare/safety_baseline.py`: death, serious and named-event sentences with their numbers). The model reader is built too (`compare/safety_llm.py`, run with `read-safety`): one call per abstract, every statement checked by code, the LLM answer and any enabled baseline answer stored separately in `safety_readings` / `safety_statements`. The comparison rules are built as plain code (`compare/safety_compare.py`: definition, group, registry number, population, then the numbers); the roll-up (`compare/safety_rollup.py`) gives one finding per safety quantity per trial (deaths, serious events, each named event) in the report and the review queue. The original safety evaluation (S9) is complete; its limits and the unrun independent v2 holdout are documented in `LLM_IMPROVEMENT_ITERATION.md`.

```mermaid
flowchart TD
  REG[("adverse events posted in the registry<br/>only for trials with posted results")] --> SP["parse into SafetyEvent<br/>event, arm, count, denominator, period, grade, threshold"]
  PAP[("confirmed paper abstract")] --> SX["LLM extraction<br/>safety events, each field with a quote"]
  SX --> SV["code check: quotes appear in the text"]
  SP --> SM["match events, code first<br/>then bounded LLM question"]
  SV --> SM
  SM --> SC{"fields needed<br/>to compare present?"}
  SC -->|"no"| NE["not enough detail to compare<br/>never called a discrepancy"]
  SC -->|"yes"| SR["compare counts, denominators, periods<br/>units normalised first"]
  SR --> FD["Findings for review"]
  NE --> FD

  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  class REG,SP,PAP,SX,SV,SM,SC,NE,SR,FD built
```

Key points
- A missing denominator, period or grade means "not enough detail to compare", never a discrepancy.
- Different reporting thresholds or periods are not discrepancies by themselves.

---

## 3B. Dashboard workflow (2026-11-13, D85)

```mermaid
flowchart TD
  G["Google sign-in<br/>email on the allowlist?"] -->|"no"| X["refused"]
  G -->|"yes: email becomes the reviewer name"| U["user enters an NCT ID"]
  U --> S{"stored already?"}
  S -->|"yes"| V["show outcomes, papers, findings (free)"]
  S -->|"no, or user clicks Analyze Trial"| F["ingest, ingest-papers (free)"]
  F --> E{"new model calls<br/>(stored answers reused)"}
  E -->|"5 or fewer"| A["match-papers, at most 10 papers per trial"]
  E -->|"more"| C["user confirms Run or Cancel"]
  C --> A
  A --> V
  V --> Q["review queue: Needs review / Human-verified / Unresolved"]
  Q --> R["Accept, Correct or Unable to determine<br/>saved in paper_match_reviews<br/>model answer unchanged"]
  R --> Q
  Q --> X["Download CSV or report<br/>final status only if Human-verified"]
  Q --> T["All trials tab: counts over every trial,<br/>per-trial table, click a row to open it"]
  V --> RC["Registry changes panel:<br/>outcomes added, removed or edited between versions"]
  G --> SP["Sponsors tab: results reporting counts, no score"]
  G --> PT["Patterns tab: fixed questions, lists of trials"]
```

## 3C. Deployment workflow (Docker, CI/CD, and Google Cloud, updated 2026-11-19)

```mermaid
flowchart TD
  L["laptop: Dockerfile + docker-compose<br/>Postgres, API, dashboard"] --> B["gcloud builds submit"]
  GH["GitHub push or pull request"] --> CI["Tests with PostgreSQL and Docker build check"]
  CI -->|"Successful main run"| CD["Deploy workflow<br/>Workload Identity Federation"]
  CD --> AR
  B --> AR[("Artifact Registry<br/>dashboard image")]
  AR --> CR["Cloud Run: trialtrace-dashboard<br/>0 to 2 instances, public URL"]
  SM[("Secret Manager<br/>database URL, OpenAI key,<br/>allowed emails, sign-in file")] --> CR
  CR -->|"Cloud SQL connector"| SQL[("Cloud SQL Postgres 16")]
  CR -->|"sign-in"| GO["Google OAuth<br/>redirect URI = service URL + /oauth2callback"]
  CR -->|"Analyze Trial only"| OA["OpenAI model"]
  U["reviewer browser"] -->|"https"| CR
  ING["Configured local ingestion"] --> LOCAL[("Local raw files and metadata")]
  ING -->|"Create-only backup uploads"| GCS[("Cloud Storage<br/>trialtrace-514821-raw")]

  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  classDef planned fill:#e5e7eb,stroke:#6b7280,color:#374151
  class L,B,AR,CR,SM,SQL,GO,OA,U built
```

Key points
- The same image runs locally (compose) and on Cloud Run. Cloud Run supplies the port; the start command copies the mounted sign-in file into place.
- The service URL is public, so the app is the gate: Google sign-in plus an email allowlist. With no allowed emails nobody gets in.
- Only the dashboard is deployed. The API is optional and not deployed. After changing a secret, start a new Cloud Run revision.
- Cloud Storage is the deployed raw-source backup. Configured ingestion performs uploads independently of the dashboard runtime. Cloud SQL stores parsed data and evidence. The CI/CD workflow deploys application images; it does not synchronize raw sources or databases.
- Steps and fixes: `DEPLOY_GCP.md`. The full design: `SYSTEM_DESIGN.md`.

## 4. Final combined workflow

```mermaid
flowchart LR
  subgraph SRC["Sources"]
    CT["ClinicalTrials.gov"]
    PM["PubMed"]
  end

  subgraph ING["Ingest, with retries and recorded failures"]
    I1["registry ingest"]
    I2["PubMed search, fetch, parse, link check"]
  end

  subgraph STORE["Stored evidence"]
    RAW[("raw files<br/>JSON and XML, by hash")]
    GCS[("Cloud Storage<br/>raw files and metadata backup")]
    TBL[("database<br/>snapshots, observations, evidence,<br/>outcomes, papers, links, failures")]
  end

  subgraph ANS["Answers"]
    A1["Q1 results posted"]
    A2["Q2 timeline"]
    A3["Q3 sponsors<br/>Q4 patterns"]
    A4["Q5a registry changes"]
    A5["Q5b reported outcomes match?<br/>results arm now, paper arm later"]
    A6["Q6 side effects match?"]
  end

  CT --> I1
  PM --> I2
  I1 --> RAW
  I2 --> RAW
  I1 -->|"Configured backup"| GCS
  I2 -->|"Configured backup"| GCS
  I1 --> TBL
  I2 --> TBL
  TBL --> A1
  TBL --> A2
  TBL --> A3
  TBL --> A4
  TBL --> A5
  TBL --> A6

  A1 --> FD["Findings<br/>evidence + limitations"]
  A2 --> FD
  A3 --> RP
  A4 --> FD
  A5 --> FD
  A6 --> FD
  FD --> RP["Report<br/>a saved run"]
  RP --> RV["Human review<br/>approve, edit, reject"]
  RV --> HIST[("review history")]
  FD --> EV["Evaluation<br/>absolute metrics and coverage,<br/>held-out result"]

  classDef built fill:#d1fae5,stroke:#059669,color:#064e3b
  classDef next fill:#fef3c7,stroke:#d97706,color:#78350f
  classDef planned fill:#e5e7eb,stroke:#6b7280,color:#374151
  class CT,PM,I1,I2,RAW,TBL,A1,A2,A3,A4,FD,RP,RV,HIST built
  class A5,A6,EV built
```

### Which question uses which source

| Question | Source | Method | Status |
|---|---|---|---|
| 1 Results posted? | Registry | Code | Built |
| 2 How long since completion? | Registry | Code | Built |
| 3 Results per sponsor | Registry | SQL | Built |
| 4 Patterns across trials | Registry | SQL | Built |
| 5a Registered outcomes changed? | Registry, two versions | Code | Built |
| 5b (results arm) Posted results match the registered outcomes? | Registry only | Code | Built |
| 5b (paper arm) Paper reports the registered outcomes? | Registry + PubMed | LLM reads, code checks and decides | Built, evaluated on small sets |
| 6 Side effects match? | Registry + PubMed | LLM reads safety statements, code checks and compares | Built; first evaluation done, comparison not validated |

### What a person sees

1. `python -m trialtrace ingest ...` loads trials.
2. `python -m trialtrace ingest-papers` finds and stores their PubMed papers.
3. `python -m trialtrace report` builds a report and saves it as a run.
4. `python -m trialtrace review --reviewer "Name"` records decisions about differences.
5. Nothing learns by itself: reviewer reasons are stored, and a person decides how to improve the rules, which must pass the evaluation first.
