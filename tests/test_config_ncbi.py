import pytest

from trialtrace.config import Settings


@pytest.mark.parametrize("key", ["", "   "])
def test_empty_optional_ncbi_key_is_omitted(monkeypatch, key):
    monkeypatch.setenv("NCBI_API_KEY", key)
    assert Settings(_env_file=None).ncbi_api_key is None


def test_configured_ncbi_key_is_preserved(monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "test-key")
    assert Settings(_env_file=None).ncbi_api_key.get_secret_value() == "test-key"
