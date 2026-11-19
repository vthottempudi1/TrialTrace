# Deploying the dashboard to Google Cloud

The dashboard runs on Cloud Run with its database on Cloud SQL. Only the dashboard is deployed; the API is not. Login is Google sign-in with an email allowlist. Replace `<project>` with your project ID and `<number>` with its project number.

## What runs where

| Piece | Service | Notes |
|---|---|---|
| Dashboard | Cloud Run `trialtrace-dashboard` | scales to zero, at most 2 copies, public URL |
| Database | Cloud SQL Postgres 16 `trialtrace-db` | smallest size, about $10 to $15 a month |
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

## Things that went wrong, and the fix

- Quoting the start command in PowerShell: `${PORT}` was expanded to nothing. Use single quotes.
- `cp` of the mounted secret failed ("replaced while being copied"). Use `cat file > target`.
- After resetting the OAuth client secret, the cloud copy was stale. Add a new Secret Manager version and start a new revision, because Cloud Run reads `latest` only when an instance starts.

## Safety notes

- The public URL is protected only by the sign-in and the allowlist. Anyone not on the list is refused after signing in.
- Never put a client secret in a file that is not ignored by git and Docker (`.streamlit/secrets.toml.example` is not ignored; it holds placeholders only).
- To stop the database cost: `gcloud sql instances patch trialtrace-db --activation-policy=NEVER`.
