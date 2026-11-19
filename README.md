# TrialTrace

### Clinical Trial Evidence Intelligence Platform

TrialTrace helps reviewers trace clinical-trial reporting back to its sources. It brings together ClinicalTrials.gov records and linked PubMed abstracts, checks whether registered outcomes are reported, and presents findings with supporting evidence and a review history.

The project combines deterministic registry checks, an LLM abstract reader, evidence validation, and human review in a Streamlit dashboard.

[Open the dashboard](https://trialtrace-dashboard-1061960202985.us-central1.run.app)

The hosted dashboard requires Google sign-in with an allowlisted email.

## Project versions

| Version | Scope | Status |
| --- | --- | --- |
| **v1 · Registry and abstract validation** | Validate registered trial outcomes against results published in the registry and linked paper abstracts. Track changes between captured registry versions, results-posting status, completion timelines, and sponsor reporting counts | Implemented |
| **v2 · Safety** | Extract safety statements from abstracts and compare them with registry adverse-event evidence | Separate workstream in `trialtrace/safety/`, currently paused |
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
    DB[("PostgreSQL<br/>Snapshots, evidence, papers, matches, reviews")]
    MODEL["OpenAI abstract reader"]
    DASH["Streamlit dashboard<br/>Google sign-in and reviewer decisions"]
    API["Optional FastAPI service<br/>Stored data, reviews, exports"]
    EXPORT["CSV and Markdown exports"]

    CT --> ING
    PM --> ING
    ING --> RAW
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

The dashboard uses the Python application and PostgreSQL directly. The optional API exposes stored data, exports, and review operations. Raw downloads preserve the source material behind parsed evidence. Safety v2 modules are grouped under `trialtrace/safety/` while that workstream is paused.

### From a trial to a reviewed finding

1. **Capture the registry record.** Save the raw response, record its content hash, and extract outcomes and citable evidence. Preserve each distinct version captured.
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

Database tests use a separate `trialtrace_test` database and roll back test transactions. The PostgreSQL account needs permission to create that database on the first run. Tests use mocked external services. The GitHub Actions workflow defines PostgreSQL-backed tests and a Docker image build.

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

The dashboard is deployed using Google Cloud Run and Cloud SQL. The [deployment guide](docs/DEPLOY_GCP.md) describes its configuration, and [workflow diagrams](docs/WORKFLOWS.md) expand on the data flow.
