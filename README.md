<div align="center">

# TrialTrace: Clinical Trial Evidence Intelligence Platform

Evidence-grounded review of clinical-trial reporting: registry checks, an LLM abstract reader with code-verified quotes, source evidence for every finding, and human review with an audit history. Deployed on Google Cloud with Google sign-in.

![Python](https://img.shields.io/badge/python-3.12-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-dashboard-FF4B4B)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-structured_data-4169E1)
![OpenAI](https://img.shields.io/badge/OpenAI-abstract_reader-412991)
![ClinicalTrials.gov](https://img.shields.io/badge/ClinicalTrials.gov-registry_source-0B5394)
![PubMed](https://img.shields.io/badge/PubMed-paper_source-326599)
![Human review](https://img.shields.io/badge/Human_review-audit_history-7A1FA2)
![Docker](https://img.shields.io/badge/Docker-containerized-2496ED)
![Cloud Run](https://img.shields.io/badge/Google_Cloud_Run-deployed-4285F4)
![Cloud SQL](https://img.shields.io/badge/Cloud_SQL-PostgreSQL_16-4285F4)
![Auth](https://img.shields.io/badge/Google_sign--in-email_allowlist-34A853)
[![CI](https://github.com/vthottempudi1/TrialTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/vthottempudi1/TrialTrace/actions/workflows/ci.yml)

**Registry Checks + LLM Abstract Reader + Quote Validation + Human Review + CI/CD to Cloud Run**

[Open the dashboard](https://trialtrace-dashboard-1061960202985.us-central1.run.app) (Google sign-in with an allowlisted email)

</div>

TrialTrace helps reviewers trace clinical-trial reporting back to its sources. It brings together ClinicalTrials.gov records and linked PubMed abstracts, checks whether registered outcomes are reported, and presents findings with supporting evidence and a review history.

The project combines deterministic registry checks, an LLM abstract reader, evidence validation, and human review in a Streamlit dashboard.

## Project versions

| Version | Scope | Status |
| --- | --- | --- |
| **v1 · Registry and abstract validation** | Validate registered trial outcomes against results published in the registry and linked paper abstracts. Track changes between captured registry versions, results-posting status, completion timelines, and sponsor reporting counts | Implemented |
| **v2 · Safety** | Extract safety statements from abstracts and compare them with registry adverse-event evidence | Planned extension |
| **v3 · Full-paper text analysis** | Validate registered outcomes against full paper texts and trace supporting evidence across paper sections | Planned extension |

**All safety-related work belongs to project v2.** This includes adverse-event ingestion and parsing, database tables and migrations, statement extraction, numeric and time-window comparisons, assisted matching, review tools, and supporting documentation.

## What a reviewer can do

- **Inspect a trial.** Open an NCT ID and explore registry outcomes, linked papers, findings, and cited evidence.
- **Check reporting.** View results-posting status, time since primary completion, and differences between registered and posted outcomes.
- **Trace outcomes across papers.** Review identified, unclear, and not-identified outcomes alongside highlighted abstract quotes.
- **Review findings.** Accept, correct, or mark a finding as undetermined, with a reason and an audit history.
- **Explore the stored corpus.** View sponsor reporting counts and fixed cross-trial queries.
- **Export findings.** Download CSV and Markdown reports.

The dashboard provides All trials, One trial, Assisted review, Sponsors, and Patterns views. Review queues distinguish Needs review, Human-verified, and Unresolved findings.

## v1 evaluation results

The LLM abstract reader was scored on trials it had never seen, against targets written down before the run (design decisions D93, D42, D101). Ranges are 95% Wilson intervals. Counts are small, so treat these as point estimates.

| Measure | Result | 95% range | Set |
| --- | --- | --- | --- |
| Precision (when it says "reported", is it right?) | **91%** (20 of 22) | 72–97% | 19 unseen trials, target 90% |
| Recall (reported outcomes found with a real quote) | **80%** (20 of 25) | 61–91% | same set, target 80% |
| Made-up quotes or failed reads | **0 / 0** | | 204 model calls; quotes are checked by code |
| Precision and recall on a second frozen set | **85% / 85%** | 66–94% | 19 trials, 72 outcomes |
| LLM reader vs plain keyword code (recall) | **85% vs 31%** | 66–94% vs 17–50% | second frozen set |
| Human agreement with the labels | **90%** (27 of 30), kappa 0.85 | | 30 pairs labelled blind |

About 42% of outcomes are called "unclear" and sent to a reviewer. That is routing, not a wrong answer. Labels were partly AI-drafted and checked by a person, and the sets are small.

## Architecture

```mermaid
flowchart TB
    subgraph Sources["External sources"]
        CT["ClinicalTrials.gov API"]
        PM["PubMed E-utilities"]
    end
    subgraph Pipeline["Python processing pipeline"]
        ING["Fetch and parse registry records and papers"]
        LINK["Check paper-to-trial links"]
        REG["v1 Registry checks"]
        MATCH["v1 Outcome-abstract matching"]
        GUARD["Validate quotes and trial identity"]
        ROLL["Combine evidence per outcome"]
        REPORT["Build findings and review queue"]
    end
    RAW[("Raw source files by content hash")]
    GCS[("Google Cloud Storage<br/>Raw files and metadata backup")]
    DB[("PostgreSQL<br/>Snapshots, evidence, papers, matches, reviews")]
    MODEL["OpenAI abstract reader"]
    DASH["Streamlit dashboard<br/>Google sign-in and reviewer decisions"]
    API["Optional FastAPI service<br/>Stored data, reviews, exports"]
    EXPORT["CSV and Markdown exports"]

    CT --> ING
    PM --> ING
    ING --> RAW
    ING -->|"Configured ingestion backup"| GCS
    ING --> DB
    DB --> LINK
    LINK --> MATCH
    DB --> MATCH
    MATCH <--> MODEL
    MATCH --> GUARD
    GUARD --> DB
    DB --> REG
    DB --> ROLL
    REG --> REPORT
    ROLL --> REPORT
    REPORT --> DB
    DB --> DASH
    DASH -->|"Append review decisions"| DB
    DB --> API
    DASH --> EXPORT
    API --> EXPORT
```

The dashboard uses the Python application and PostgreSQL directly. The optional API exposes stored data, exports, and review operations. Ingestion saves raw downloads locally and, when `TRIALTRACE_RAW_BUCKET` is configured, uploads the files and metadata to Google Cloud Storage. The deployed bucket is `gs://trialtrace-514821-raw` in `us-central1`. The current hosted dashboard reads its evidence from PostgreSQL; bucket uploads are performed by the configured ingestion process.

### From a trial to a reviewed finding

1. **Capture the registry record.** Save the raw response and metadata locally, copy them to the configured Cloud Storage bucket, and extract outcomes and citable evidence. Preserve each distinct content version with its hash.
2. **Collect candidate papers.** Retrieve PubMed records and abstracts, then check their connection to the trial.
3. **Read each outcome-paper pair.** Give the LLM the registered outcome and abstract. Store its answer, supporting quote, and model metadata. An optional rules baseline provides a separate comparison.
4. **Validate and combine evidence.** Check quoted text and offsets, apply trial-identity safeguards, and combine paper-level answers into one finding per registered outcome.
5. **Review and export.** Present source evidence and record reviewer decisions separately from original predictions.

Stored answers can be reused when inputs and model configuration match. The dashboard estimates new model calls and requests confirmation when an analysis needs more than five calls.

### Registry snapshots and evidence scope

The v1 registry and abstract workflow uses outcomes from the latest saved registry snapshot. Registry-change detection compares the two most recently captured distinct versions in the database. With insufficient history, the result is unknown.

Each captured registry snapshot has a content hash and capture date, allowing findings to be traced to the record used for analysis.

**Not identified** in v1 means supporting reporting was not identified in the available abstract text. Full-paper text analysis belongs to v3. Findings describe evidence and differences for human review. Sponsor summaries describe the ingested corpus, and missing data retain an explicit unknown state.

## Run locally

Requires **Python 3.12 or newer**, PostgreSQL, and an OpenAI API key for model-based matching. An optional NCBI key increases PubMed request capacity.

```bash
pip install -e ".[dev,dashboard]"
```

Create a `.env` file at the project root.

```dotenv
TRIALTRACE_DATABASE_URL=postgresql://user:password@localhost:5432/trialtrace
OPENAI_API_KEY=your_openai_key
# Optional
NCBI_API_KEY=your_ncbi_key
# Local development
TRIALTRACE_DISABLE_LOGIN=1
```

Cloud raw-source backup is deployed in `gs://trialtrace-514821-raw`. To enable uploads from your ingestion environment, install `pip install -e ".[cloud]"`, configure Application Default Credentials, and set `TRIALTRACE_RAW_BUCKET=trialtrace-514821-raw`. Each upload uses a create-only object precondition. An upload failure is logged while local ingestion continues, and an operator can reconcile missing objects. See the [storage setup and recovery steps](docs/DEPLOY_GCP.md#raw-source-files-in-cloud-storage-added-2026-11-19).

The database URL above is for a local PostgreSQL only. On Google Cloud the hosted dashboard gets its URL from Secret Manager, and it points to the separate Cloud SQL database (see the [deployment guide](docs/DEPLOY_GCP.md)). The two databases are not synced.

Start PostgreSQL with a database matching that connection string, then run the workflow.

```bash
python -m trialtrace.db.migrate
python -m trialtrace ingest NCT04644237
python -m trialtrace ingest-papers NCT04644237
python -m trialtrace match-papers NCT04644237
python -m trialtrace report
streamlit run dashboard/app.py
```

`match-papers` makes paid model calls for uncached pairs. Add `--with-baseline` to store a rules-based comparison, or `--refresh` to request new answers. Review findings through the command line with the following command.

```bash
python -m trialtrace review --reviewer "Your Name"
```

### Sign-in

For Google sign-in, set `TRIALTRACE_ALLOWED_EMAILS` to a comma-separated allowlist, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`, and configure Google OAuth credentials. Set `TRIALTRACE_DISABLE_LOGIN=0` to enable authentication. An empty allowlist blocks access when authentication is enabled.

Keep `.env` and `.streamlit/secrets.toml` out of version control. See the [Google Cloud deployment guide](docs/DEPLOY_GCP.md) for hosted configuration.

### Docker and the optional API

```bash
docker compose up --build
```

Compose starts PostgreSQL, the API at `http://localhost:8000`, and the dashboard at `http://localhost:8501`. Supply the environment and login configuration described above.

To run the API separately, install its extra and start Uvicorn.

```bash
pip install -e ".[api]"
uvicorn trialtrace.api:app --port 8000
```

API documentation is available at `http://localhost:8000/docs`. The service supports inspecting stored trials and findings, estimating matching calls, submitting reviews, and exporting findings. Model execution is initiated through the CLI or dashboard.

### Tests

```bash
python -m pytest
```

Database tests use a separate `trialtrace_test` database and roll back test transactions. The PostgreSQL account needs permission to create that database on the first run. Tests use mocked external services. The GitHub Actions workflow runs PostgreSQL-backed tests and a Docker image build on every push.

## Repository layout

```text
trialtrace/
  sources/          Registry and PubMed clients, raw-source storage
  parse/            Registry, posted-result, and paper parsers
  checks/           Results status, timelines, links, sponsor counts, patterns
  compare/          Registry differences and outcome-paper evidence matching
  safety/           v2 safety modules and safety migrations
  db/               PostgreSQL repository and core migrations
  review/           Review queues, decisions, highlighting, exports
  match_papers.py   v1 outcome-abstract matching, answer reuse, call estimates
  report.py         Findings and report generation
  api.py            Optional FastAPI service
  cli.py            Command-line entry points
dashboard/          Streamlit interface
evals/              Evaluation tools, local datasets and run records
tests/              Automated tests
docs/               Workflows, deployment, and design decisions
```

The dashboard is deployed using Google Cloud Run and Cloud SQL, with raw-source backup in Google Cloud Storage. **CI/CD:** every push to `main` runs the tests (`ci.yml`); when they pass, `deploy.yml` builds the Docker image, pushes it to Artifact Registry and updates the Cloud Run service. GitHub signs in to Google with Workload Identity Federation, so no key file is stored. The [deployment guide](docs/DEPLOY_GCP.md) describes its configuration, and [workflow diagrams](docs/WORKFLOWS.md) expand on the data flow.
