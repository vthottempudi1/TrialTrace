##############################################################################
# trialtrace/api.py
#
# PURPOSE: A small web API over what TrialTrace has stored: trials, their findings with review state, an export,
#          a free estimate of model calls, and one write: a human review of a finding.
#          It never calls the model and never downloads anything: analysing a trial costs money and stays a
#          deliberate step in the command line or the dashboard.
#          Errors: bad input is a 422 with a message; an unknown trial or finding is a 404; anything unexpected is
#          logged with its details and returned as a plain 500 that shows none of them.
#
# USE:     uvicorn trialtrace.api:app --port 8000        (needs TRIALTRACE_DATABASE_URL; docs at /docs)
##############################################################################

import logging
import re
import time
import uuid
from typing import Iterator

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from trialtrace.config import Settings
from trialtrace.match_papers import estimate_match_calls
from trialtrace.review.export_findings import export_rows, to_csv
from trialtrace.review.match_review import STATES, UNRESOLVED_REASONS, count_states, counts_by_trial, load_queue, save_review
from trialtrace.trial_view import load_outcomes, load_papers

logger = logging.getLogger("trialtrace.api")
NCT_PATTERN = re.compile(r"NCT[0-9]{8}")


class ReviewIn(BaseModel):
    """One human review of one model finding. The model's own answer is never changed."""
    reviewer: str
    decision: str                                   # accept / correct / accept_correction / undetermined
    note: str = ""
    corrected_status: str | None = None
    corrected_matched_name: str | None = None
    evidence: str = ""
    unresolved_reason: str | None = None
    review_seconds: int | None = None


def check_nct(nct_id: str) -> str:
    nct_id = nct_id.upper()
    if not NCT_PATTERN.fullmatch(nct_id):
        raise HTTPException(status_code=422, detail="An NCT ID looks like NCT followed by 8 digits.")
    return nct_id


def create_app(database_url: str | None = None) -> FastAPI:
    """Build the app. database_url=None reads TRIALTRACE_DATABASE_URL when a request arrives."""
    app = FastAPI(title="TrialTrace: Clinical Trial Evidence Intelligence Platform API", version="0.1.0",
                  description="Findings are AI-identified and provisional until reviewed. Model status is not clinical risk.")

    def get_conn() -> Iterator[psycopg.Connection]:
        url = database_url or Settings().database_url
        if url is None:
            raise HTTPException(status_code=503, detail="The database is not configured.")
        with psycopg.connect(url) as conn:
            yield conn

    app.state.get_conn = get_conn                                       # so tests can swap the connection

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:                                           # nothing here must leak to the caller
            logger.exception("request %s %s %s failed", request_id, request.method, request.url.path)
            return JSONResponse(status_code=500, content={"error": "internal error", "request_id": request_id})
        logger.info("request %s %s %s -> %s in %.0f ms", request_id, request.method, request.url.path, response.status_code,
                    1000 * (time.perf_counter() - started))
        response.headers["X-Request-Id"] = request_id
        return response

    @app.get("/health")
    def health(conn: psycopg.Connection = Depends(get_conn)) -> dict:
        conn.execute("SELECT 1")
        return {"status": "ok", "database": True}

    @app.get("/trials")
    def trials(conn: psycopg.Connection = Depends(get_conn)) -> dict:
        """Every trial with findings: its counts of Needs review, Human-verified and Unresolved."""
        queue = load_queue(conn)
        return {"counts": count_states(queue), "trials": counts_by_trial(queue)}

    @app.get("/trials/{nct_id}")
    def trial(nct_id: str, conn: psycopg.Connection = Depends(get_conn)) -> dict:
        nct_id = check_nct(nct_id)
        outcomes = load_outcomes(conn, nct_id)
        if not outcomes:
            raise HTTPException(status_code=404, detail="This trial is not stored.")
        queue = load_queue(conn, nct_id)
        return {"nct_id": nct_id, "outcomes": outcomes, "papers": load_papers(conn, nct_id),
                "counts": count_states(queue), "findings": export_rows(queue),
                "note": "Model status says what the model concluded; it is not clinical risk. final_status is set only for Human-verified findings."}

    @app.get("/trials/{nct_id}/findings.csv", response_class=PlainTextResponse)
    def findings_csv(nct_id: str, conn: psycopg.Connection = Depends(get_conn)) -> PlainTextResponse:
        nct_id = check_nct(nct_id)
        queue = load_queue(conn, nct_id)
        if not queue:
            raise HTTPException(status_code=404, detail="No findings are stored for this trial.")
        return PlainTextResponse(to_csv(export_rows(queue)), media_type="text/csv")

    @app.get("/trials/{nct_id}/estimate")
    def estimate(nct_id: str, refresh: bool = False, conn: psycopg.Connection = Depends(get_conn)) -> dict:
        """How many paid model calls an analysis would make now. Free: no model call, nothing stored."""
        nct_id = check_nct(nct_id)
        return estimate_match_calls(conn, Settings(), nct_id, refresh=refresh).model_dump()

    @app.get("/review-options")
    def review_options() -> dict:
        return {"states": list(STATES), "unresolved_reasons": UNRESOLVED_REASONS,
                "decisions": ["accept", "correct", "accept_correction", "undetermined"],
                "statuses": ["identified", "not_identified", "ambiguous"]}

    @app.post("/findings/{match_id}/review", status_code=201)
    def review(match_id: int, body: ReviewIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
        """Add a human review of a model finding. A correction is verified only after a later accept_correction."""
        exists = conn.execute("SELECT 1 FROM paper_matches WHERE match_id = %s AND system = 'llm'", (match_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="No model finding with this id.")
        try:
            review_id = save_review(conn, match_id, body.reviewer, body.decision, body.note, body.corrected_status,
                                    body.corrected_matched_name, body.evidence, body.unresolved_reason, body.review_seconds)
            conn.commit()
        except ValueError as error:                                 # the review rules (reviewer needed, correction needs a note ...)
            conn.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"review_id": review_id, "match_id": match_id}

    return app


app = create_app()
