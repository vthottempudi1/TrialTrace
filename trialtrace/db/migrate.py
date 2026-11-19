##############################################################################
# trialtrace/db/migrate.py
#
# PURPOSE: Apply every new .sql file in db/migrations/, in name order, once.
#          Applied files are remembered in the schema_migrations table, so
#          running this twice is safe.
#
# USE:     python -m trialtrace.db.migrate
##############################################################################

from pathlib import Path

import psycopg

from trialtrace.config import Settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SAFETY_MIGRATIONS_DIR = Path(__file__).parent.parent / "safety" / "migrations"


def apply_migrations(database_url: str) -> list[str]:
    """Run each not-yet-applied file. Returns the names applied this time."""
    applied_now = []
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}

        migration_paths = sorted(
            [*MIGRATIONS_DIR.glob("*.sql"), *SAFETY_MIGRATIONS_DIR.glob("*.sql")],
            key=lambda path: path.name,
        )
        for path in migration_paths:
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
        # leaving the `with` block commits everything; any error rolls it all back
    return applied_now


def main() -> None:
    url = Settings().database_url
    if url is None:
        raise SystemExit("TRIALTRACE_DATABASE_URL is not set (see .env)")
    applied = apply_migrations(url)
    print("applied:", ", ".join(applied) if applied else "nothing new")


if __name__ == "__main__":
    main()
