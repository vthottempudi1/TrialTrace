# Deploying the dashboard to Google Cloud

The dashboard runs on Cloud Run with its database on Cloud SQL. Only the dashboard is deployed; the API is not. Login is Google sign-in with an email allowlist. Replace `<project>` with your project ID and `<number>` with its project number.

## What runs where

| Piece | Service | Notes |
|---|---|---|
| Dashboard | Cloud Run `trialtrace-dashboard` | scales to zero, at most 2 copies, public URL |
| Database | Cloud SQL Postgres 16 `trialtrace-db` | smallest size, about $10 to $15 a month |
| Raw-source backup | Cloud Storage `gs://trialtrace-514821-raw` | local ingestion uploads raw source files and metadata; `us-central1` |
| Image | Artifact Registry repo `trialtrace` | built with Cloud Build from the `Dockerfile` |
| Keys | Secret Manager | database URL, OpenAI key, allowed emails, the sign-in file |

## Steps

1. Create a project, turn on billing, enable Cloud Run, Cloud SQL Admin, Secret Manager, Artifact Registry and Cloud Build.
2. Create the Cloud SQL instance, a `trialtrace` database and a `trialtrace` user with a random password.
3. Load the data. `pg_dump --no-owner --no-privileges` the local database, upload the file to a temporary bucket, `gcloud sql import sql`, then delete the bucket. A new empty database can use `python -m trialtrace.db.migrate` instead.
4. Create four secrets: the database URL (`postgresql://trialtrace:<password>@/trialtrace?host=/cloudsql/<project>:<region>:trialtrace-db`), the OpenAI key, the allowed emails, and a copy of `.streamlit/secrets.toml`.
5. Build: `gcloud builds submit . --tag <region>-docker.pkg.dev/<project>/trialtrace/dashboard:v1`.
6. Give the default compute service account the roles Secret Manager Secret Accessor and Cloud SQL Client.
7. Deploy with `gcloud run deploy`: the image, `--allow-unauthenticated` (the app does its own sign-in), `--add-cloudsql-instances`, the secrets as environment variables, and the sign-in file mounted at `/secrets/secrets.toml`. The start command copies that file to `/app/.streamlit/secrets.toml`, then runs Streamlit on `$PORT`.
8. The service URL is `https://trialtrace-dashboard-<number>.<region>.run.app`. Add `<that URL>/oauth2callback` to the OAuth client's authorized redirect URIs, and use the same value as `redirect_uri` in the cloud copy of `secrets.toml`.

## Raw source files in Cloud Storage (added 2026-11-19)

Bucket `gs://trialtrace-514821-raw` (us-central1, uniform access, public access blocked) keeps a copy of every raw download, under the same path as on the laptop: `raw/<source>/<NCT>/<hash>.json|xml` plus `.meta.json`. The 5,700 existing files were uploaded once with `gcloud storage rsync data/raw gs://trialtrace-514821-raw/raw --recursive`.

- Turn on: `pip install -e ".[cloud]"` and set `TRIALTRACE_RAW_BUCKET=trialtrace-514821-raw` in `.env`. Leave it unset to save locally only.
- Each configured `save_raw` call attempts to upload the raw file and its metadata after local persistence, including when the local file already exists. `if_generation_match=0` creates an object only when it is absent. A failed upload is logged per object, and ingestion continues. There is no automatic catch-up queue.
- Cloud Run does not read or write this bucket. Only ingest does, from the laptop. The dashboard reads the database.
- Needs `gcloud auth application-default login` and `gcloud auth application-default set-quota-project trialtrace-514821` (our first test failed with "project not found" because the quota project was the old trialsentinel one).
- Cost: about $0.02 per GB a month, so pennies.

The deployed bucket metadata was checked on 2026-11-19. Its location is `US-CENTRAL1`, uniform bucket-level access is enabled, and public-access prevention is enforced. The recorded 5,700-file upload above describes the initial transfer, rather than a continuously updated object count.

### Configure ingestion and reconcile backup files

```bash
pip install -e ".[cloud]"
gcloud auth application-default login
gcloud auth application-default set-quota-project trialtrace-514821
```

Set `TRIALTRACE_RAW_BUCKET=trialtrace-514821-raw` in the ingestion environment. Both registry and PubMed ingestion pass that setting to `save_raw`. The deployed dashboard's image and runtime configuration are managed separately from local ingestion.

To reconcile locally retained raw files while preserving existing cloud objects, run a create-only copy from the project root.

```bash
gcloud storage cp --recursive --no-clobber data/raw gs://trialtrace-514821-raw/
```

Source files and `.meta.json` are separate objects, so check failed-upload logs for either path. Re-ingesting an already retained source also attempts its backup again. `StoredRaw.raw_file_path` continues to hold a local file path. A workflow that reads that path restores the needed objects locally before use. This backup does not synchronize the local PostgreSQL and Cloud SQL databases.

## CI/CD with GitHub Actions (set up 2026-11-19)

Repo: `vthottempudi1/TrialTrace`. Two workflows in `.github/workflows/`:

- `ci.yml`: on every push, runs the tests against a Postgres container and checks that the Docker image builds.
- `deploy.yml`: after `ci` succeeds on `main` (or by hand with "Run workflow"), builds the image tagged with the commit, pushes it to Artifact Registry, runs `gcloud run services update --image`, then calls `/_stcore/health`. Only the image changes; secrets, the Cloud SQL link and settings stay on the service. If CI fails, nothing is deployed.

Google trusts GitHub through Workload Identity Federation, so no key file is stored:
- service account `github-deployer` with `run.admin`, `artifactregistry.writer`, and `serviceAccountUser` on the Cloud Run runtime account
- pool `github-pool`, provider `github-provider`, restricted to the repo `vthottempudi1/TrialTrace`

Rollback: `gcloud run services update-traffic trialtrace-dashboard --region us-central1 --to-revisions <old-revision>=100`.

## Things that went wrong, and the fix

- Quoting the start command in PowerShell: `${PORT}` was expanded to nothing. Use single quotes.
- `cp` of the mounted secret failed ("replaced while being copied"). Use `cat file > target`.
- After resetting the OAuth client secret, the cloud copy was stale. Add a new Secret Manager version and start a new revision, because Cloud Run reads `latest` only when an instance starts.

## Safety notes

- The public URL is protected only by the sign-in and the allowlist. Anyone not on the list is refused after signing in.
- Never put a client secret in a file that is not ignored by git and Docker (`.streamlit/secrets.toml.example` is not ignored; it holds placeholders only).
- To stop the database cost: `gcloud sql instances patch trialtrace-db --activation-policy=NEVER`.
