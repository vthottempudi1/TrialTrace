##############################################################################
# trialtrace/checks/track_record.py
#
# PURPOSE: Question 3: for each sponsor, how many COMPLETED trials in our
#          database show posted results, none, or unknown.
#          Always labeled "ingested corpus only": these are counts over the
#          trials we loaded, not the sponsor's whole record, and no score
#          or ranking is stored (it would go stale and read like a verdict).
#
# USE:     from trialtrace.checks.track_record import track_record
#          records = track_record(conn)
##############################################################################

import psycopg
from pydantic import BaseModel, Field, model_validator

from trialtrace.db.repository import results_by_sponsor

SCOPE_LABEL = "in the ingested corpus only"


class SponsorRecord(BaseModel):
    """Result counts for one sponsor key, over the trials we loaded."""

    sponsor_normalized: str | None              # None = trials whose sponsor is unknown
    example_name: str | None                    # one original spelling, for display
    completed_trials: int = Field(ge=0)
    with_results: int = Field(ge=0)
    without_results: int = Field(ge=0)
    unknown_results: int = Field(ge=0)
    scope: str = SCOPE_LABEL                    # travels with the numbers into every report

    @model_validator(mode="after")
    def counts_add_up(self):
        # Every trial is in exactly one bucket; a mismatch means a query bug
        if self.with_results + self.without_results + self.unknown_results != self.completed_trials:
            raise ValueError("result counts do not add up to completed trials")
        return self


def track_record(conn: psycopg.Connection) -> list[SponsorRecord]:
    """One SponsorRecord per sponsor key, most trials first."""
    return [SponsorRecord(**row) for row in results_by_sponsor(conn)]
