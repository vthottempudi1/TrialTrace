##############################################################################
# trialtrace/config.py
#
# PURPOSE: One place for every setting (database, web addresses, API key).
#          Values come from environment variables or .env; safe defaults
#          mean tests run without any secrets.
#
# USE:     from trialtrace.config import Settings
#          settings = Settings()
##############################################################################

from pathlib import Path                      # file paths that work on any OS
from pydantic import Field, SecretStr, field_validator         # Field: rules; SecretStr: prints as ****
from pydantic_settings import BaseSettings, SettingsConfigDict  # fills fields from env/.env


class Settings(BaseSettings):
    """All TrialTrace settings, with safe defaults."""

    model_config = SettingsConfigDict(
        env_prefix="TRIALTRACE_",   # database_url is read from TRIALTRACE_DATABASE_URL
        env_file=".env",            # also read the .env file
        extra="ignore",             # skip other apps' keys in .env
    )

    # Sources
    clinical_trials_base_url: str = "https://clinicaltrials.gov/api/v2"
    pubmed_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    request_timeout_seconds: float = Field(default=10.0, gt=0)  # seconds; must be positive
    pubmed_max_results: int = Field(default=50, gt=0)           # most paper IDs one search keeps
    pubmed_batch_size: int = Field(default=20, gt=0, le=200)    # most paper IDs asked for in one fetch
    ncbi_api_key: SecretStr | None = Field(default=None, validation_alias="NCBI_API_KEY")   # optional; allows more requests per second

    @field_validator("ncbi_api_key", mode="before")
    @classmethod
    def empty_ncbi_key(cls, value):
        """Compose supplies an empty string when the optional key is unset."""
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if isinstance(raw, str) and not raw.strip() else value

    # Dashboard login (Google sign-in; the sign-in keys live in .streamlit/secrets.toml)
    allowed_emails: str = ""            # comma-separated emails allowed to use the dashboard
    disable_login: bool = False         # True = no login, for use on your own computer only

    # Storage
    data_dir: Path = Path("data")       # raw downloads go here
    raw_bucket: str | None = None       # Google Cloud Storage bucket that also keeps a copy of raw downloads (TRIALTRACE_RAW_BUCKET)
    database_url: str | None = None     # None = not set; code needing it must say so

    # Model
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")  # shared name, no prefix
    model_name: str = Field(default="gpt-4o", validation_alias="OPENAI_CHAT_MODEL")   # shared name, no prefix
    llm_temperature: float | None = 0                           # None = do not send it (newer models reject 0)
    paper_reader_model: str = "gpt-6.1-sol"                    # the model that reads abstracts in the product (frozen after the v4 run)
    paper_reader_temperature: float | None = None               # None = not sent (this model rejects 0)
    paper_reader_max_papers: int = Field(default=10, gt=0)      # most listed papers read per trial (lowest PMIDs first); rarely reached
    safety_match_dir: Path = Path("outputs/safety-matches")  # evidence packets and append-only human decisions
    llm_timeout_seconds: float = Field(default=60.0, gt=0)      # a model answer can take longer than a web page
