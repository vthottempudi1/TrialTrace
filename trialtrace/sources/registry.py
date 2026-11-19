##############################################################################
# trialtrace/sources/registry.py
#
# PURPOSE: Ask ClinicalTrials.gov for one trial and report what happened:
#          "ok", "not_found" or "unavailable". A failed call is never
#          returned as an empty trial (unknown is unknown).
#          Returns raw bytes only; parsing is a later lesson.
#
# USE:     with httpx.Client() as http:
#              result = fetch_study(http, Settings(), "NCT04644237")
##############################################################################

import re
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel

from trialtrace.config import Settings
from trialtrace.contracts import NCT_ID_PATTERN


class RegistryFetch(BaseModel):
    """What happened when we asked the registry for one trial."""

    nct_id: str
    url: str
    status: Literal["ok", "not_found", "unavailable"]
    body: bytes | None = None               # only filled when status is "ok"
    retrieved_at: datetime
    error_code: str | None = None           # why it failed: "timeout", "http_500"...


def fetch_study(http: httpx.Client, settings: Settings, nct_id: str) -> RegistryFetch:
    """Download one trial. Never raises for network problems; reports them."""
    # Only a valid ID may go into the URL, never free text
    if not re.fullmatch(NCT_ID_PATTERN, nct_id):
        raise ValueError(f"not a valid NCT ID: {nct_id!r}")

    url = f"{settings.clinical_trials_base_url}/studies/{nct_id}"
    retrieved_at = datetime.now(timezone.utc)

    def failed(status: str, error_code: str | None = None) -> RegistryFetch:
        return RegistryFetch(
            nct_id=nct_id, url=url, status=status,
            retrieved_at=retrieved_at, error_code=error_code,
        )

    try:
        response = http.get(url, timeout=settings.request_timeout_seconds)
    except httpx.TimeoutException:          # must come first: it is a kind of HTTPError
        return failed("unavailable", "timeout")
    except httpx.HTTPError:                 # any other network problem
        return failed("unavailable", "network_error")

    if response.status_code == 200:
        return RegistryFetch(
            nct_id=nct_id, url=url, status="ok",
            body=response.content, retrieved_at=retrieved_at,
        )
    if response.status_code == 404:
        return failed("not_found")
    return failed("unavailable", f"http_{response.status_code}")   # 500, 429...: server trouble, not "no trial"
