##############################################################################
# tests/test_config.py
#
# PURPOSE:
#   Prove config.py works without any .env file or secrets,
#   and that bad values are refused.
##############################################################################

import pytest
from pydantic import ValidationError

from trialtrace.config import Settings


def test_defaults_need_no_secrets(monkeypatch):
    # _env_file=None ignores .env; delenv ignores the CI database setting, so the test is the same everywhere
    monkeypatch.delenv("TRIALTRACE_DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.pubmed_base_url.endswith("/entrez/eutils")
    assert settings.database_url is None


def test_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, request_timeout_seconds=0)


def test_pubmed_limits_have_safe_defaults_and_must_be_positive():
    settings = Settings(_env_file=None)
    assert settings.pubmed_max_results == 50 and settings.pubmed_batch_size == 20 and settings.ncbi_api_key is None
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pubmed_max_results=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pubmed_batch_size=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pubmed_batch_size=500)


def test_api_key_is_hidden_when_printed(monkeypatch):
    # monkeypatch sets an environment variable just for this test
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    settings = Settings(_env_file=None)
    assert "sk-test-123" not in str(settings.openai_api_key)
    assert settings.openai_api_key.get_secret_value() == "sk-test-123"
