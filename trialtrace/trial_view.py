##############################################################################
# trialtrace/trial_view.py
#
# PURPOSE: Read-only lookups of what is stored about one trial (outcomes, papers, abstract), shared by the
#          dashboard and the API so there is one copy. Plain SELECTs; nothing is changed.
#
# USE:     outcomes = load_outcomes(conn, "NCT04644237")
##############################################################################

from psycopg.rows import dict_row


def query(conn, sql: str, params: tuple) -> list[dict]:
    """Run one SELECT and return the rows as dicts."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def load_outcomes(conn, nct_id: str) -> list[dict]:
    """The registered outcomes of the newest stored registry version of the trial."""
    sql = ("SELECT r.role, r.position, r.measure_text AS outcome, r.time_frame FROM registered_outcomes r "
           "JOIN latest_snapshots s ON s.nct_id = r.nct_id AND s.content_hash = r.content_hash "
           "WHERE r.nct_id = %s ORDER BY r.role, r.position")
    return query(conn, sql, (nct_id,))


def load_papers(conn, nct_id: str) -> list[dict]:
    """The papers stored for the trial, each with its link verdict. They are candidates, not confirmed reports."""
    sql = ("SELECT p.pmid, p.title, p.journal, p.year, l.status AS link FROM paper_links l "
           "JOIN papers p ON p.pmid = l.pmid AND p.raw_hash = l.raw_hash WHERE l.nct_id = %s ORDER BY p.pmid")
    return query(conn, sql, (nct_id,))


def load_abstract(conn, pmid: str, raw_hash: str) -> str | None:
    """The saved abstract of the paper version the model read."""
    rows = query(conn, "SELECT abstract FROM papers WHERE pmid = %s AND raw_hash = %s", (pmid, raw_hash))
    return rows[0]["abstract"] if rows else None
