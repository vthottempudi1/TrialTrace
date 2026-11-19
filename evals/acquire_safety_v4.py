"""Fresh metadata-only draw for v6 validation. No models and no old-source edits."""
from pathlib import Path

from evals import acquire_safety_v3 as acquisition


def main():
    acquisition.FOLDER = Path("evals/safety_v4_acquisition")
    acquisition.SEED = 20261113
    acquisition.RULE = """# Safety v4 acquisition rule

Written before the new API draw and before reading candidate abstracts.
Query completed phase 3 trials with results, first posted by 2023-12-31;
take the first 500 records ordered by last-update date descending. Keep the
exact query/response. Remove all trials already stored at this point (including
all prior safety evaluations), sort remaining IDs, shuffle once with seed
20261113, and ingest the first 60. Keep the saved draw on resume; do not redraw.
Registry/PubMed failures and absence of papers remain in acquisition receipts.
The naturalistic pool requires posted registry safety and at least one clean
listed abstract. Production selection is numeric PMID ascending, at most ten;
prior project PMIDs/text are coverage-only, never strict validation cases.
Draw 30 eligible trials with the same seed; keep all remaining eligible trials
in reserve. Extend in saved order if fewer than 60 strict target statements are
labelled. No selection based on model output; no model call before actual review
and a hashed freeze. v2/v3 and guard-replay cases remain development data.
A separately constructed challenge set covers rare branches and is never
pooled with the naturalistic estimates or called real-world validation.
"""
    acquisition.main()


if __name__ == "__main__":
    main()
