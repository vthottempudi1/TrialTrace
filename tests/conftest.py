##############################################################################
# tests/conftest.py
#
# PURPOSE: Shared test setup. Database tests run against their OWN database,
#          "trialtrace_test", never the real "trialtrace" one, so real data can
#          never change a test result and a test can never touch real data.
#          The test database is created if missing and migrated automatically.
#          Every test also runs inside a transaction that is rolled back.
##############################################################################

from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from trialtrace.config import Settings
from trialtrace.db.migrate import apply_migrations

TEST_DATABASE_NAME = "trialtrace_test"


def with_database(url: str, database_name: str) -> str:
    """The same connection URL, pointing at a different database."""
    return urlunsplit(urlsplit(url)._replace(path="/" + database_name))


@pytest.fixture(scope="session")
def database_url() -> str:
    """URL of the test database. Created and migrated once per test run."""
    real_url = Settings().database_url
    if real_url is None:
        pytest.skip("TRIALTRACE_DATABASE_URL is not set")

    # CREATE DATABASE cannot run inside a transaction, hence autocommit; we connect via "postgres"
    with psycopg.connect(with_database(real_url, "postgres"), autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE_NAME,)).fetchone()
        if not exists:
            admin.execute(f"CREATE DATABASE {TEST_DATABASE_NAME}")      # a fixed name, not user input

    test_url = with_database(real_url, TEST_DATABASE_NAME)
    apply_migrations(test_url)
    return test_url


@pytest.fixture
def conn(database_url):
    """A connection whose work is undone at the end of the test."""
    connection = psycopg.connect(database_url)
    connection.execute("SELECT 1")          # open a transaction first, so the rollback undoes everything
    yield connection
    connection.rollback()
    connection.close()
