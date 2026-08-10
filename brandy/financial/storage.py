"""
Financial data persistence layer.

Insert and query companies, filings, metrics, WACC, and raw payloads.
"""

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


# ── Companies ───────────────────────────────────────────────────────

def upsert_company(conn: sqlite3.Connection, ticker: str, company_name: str = None, cik: str = None) -> int:
    """Insert or update a company, return its id."""
    row = conn.execute("SELECT id FROM companies WHERE ticker = ?", (ticker,)).fetchone()
    if row:
        if company_name or cik:
            conn.execute(
                "UPDATE companies SET company_name = COALESCE(?, company_name), cik = COALESCE(?, cik) WHERE id = ?",
                (company_name, cik, row["id"]),
            )
            conn.commit()
        return row["id"]

    cursor = conn.execute(
        "INSERT INTO companies (ticker, company_name, cik) VALUES (?, ?, ?)",
        (ticker, company_name, cik),
    )
    conn.commit()
    return cursor.lastrowid


# ── Filings ─────────────────────────────────────────────────────────

def upsert_filing(
    conn: sqlite3.Connection,
    company_id: int,
    accession: str,
    filing_date: str,
    fiscal_year: int,
    fiscal_period: str = "FY",
    form_type: str = "10-K",
) -> int:
    """Insert or get a filing, return its id."""
    row = conn.execute(
        "SELECT id FROM sec_filings WHERE company_id = ? AND accession = ?",
        (company_id, accession),
    ).fetchone()
    if row:
        return row["id"]

    cursor = conn.execute(
        """INSERT INTO sec_filings (company_id, accession, filing_date, fiscal_year, fiscal_period, form_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (company_id, accession, filing_date, fiscal_year, fiscal_period, form_type),
    )
    conn.commit()
    return cursor.lastrowid


# ── Financial metrics ───────────────────────────────────────────────

METRIC_COLUMNS = [
    "revenue", "cost_of_revenue", "gross_profit", "operating_expenses",
    "sga_expense", "operating_income", "net_income", "eps_diluted",
    "total_assets", "total_liabilities", "shareholders_equity", "cash_and_equivalents",
    "operating_cash_flow", "capital_expenditures", "depreciation_amortization",
    "tax_expense", "pretax_income",
    "short_term_debt", "long_term_debt", "total_debt", "interest_expense",
    "operating_lease_liability_current", "operating_lease_liability_noncurrent",
    "ebitda", "ebitda_source", "ebitda_notes",
    "gross_profit_source", "gross_profit_notes", "operating_income_source",
    "free_cash_flow", "gross_margin", "operating_margin", "net_margin",
    "roe", "debt_to_equity",
    "data_source",
]


def insert_financial_metrics(conn: sqlite3.Connection, filing_id: int, metrics: dict) -> int:
    """
    Replace the row of financial metrics for a filing.

    Re-running the pipeline for a ticker previously appended a second row for
    the same filing rather than superseding it. Downstream readers take the
    first match, so stale rows from an earlier run could shadow fresh ones —
    which is how corrected metrics could still surface as empty.
    """
    conn.execute("DELETE FROM financial_metrics WHERE filing_id = ?", (filing_id,))

    metrics["filing_id"] = filing_id
    cols = ["filing_id"] + [c for c in METRIC_COLUMNS if c in metrics]
    placeholders = ", ".join(["?"] * len(cols))
    col_names = ", ".join(cols)
    values = [metrics[c] for c in cols]

    cursor = conn.execute(
        f"INSERT INTO financial_metrics ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    logger.info("Inserted financial metrics for filing_id=%d", filing_id)
    return cursor.lastrowid


def get_company_metrics(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    """Retrieve all financial metrics for a ticker, ordered by fiscal year."""
    rows = conn.execute(
        """SELECT fm.*, sf.fiscal_year, sf.filing_date, c.ticker
           FROM financial_metrics fm
           JOIN sec_filings sf ON fm.filing_id = sf.id
           JOIN companies c ON sf.company_id = c.id
           WHERE c.ticker = ?
           ORDER BY sf.fiscal_year DESC""",
        (ticker,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── WACC ────────────────────────────────────────────────────────────

def insert_wacc(conn: sqlite3.Connection, company_id: int, fiscal_year: int, wacc_dict: dict) -> int:
    """
    Replace the WACC inputs + calculated result for a company-year.

    As with financial metrics, a company-year has exactly one current WACC;
    appending on re-run would let a stale calculation shadow the latest one.
    """
    conn.execute(
        "DELETE FROM wacc_inputs WHERE company_id = ? AND fiscal_year = ?",
        (company_id, fiscal_year),
    )

    wacc_dict["company_id"] = company_id
    wacc_dict["fiscal_year"] = fiscal_year

    cols = [k for k in wacc_dict if k not in ("company_id", "fiscal_year")]
    all_cols = ["company_id", "fiscal_year"] + cols
    placeholders = ", ".join(["?"] * len(all_cols))
    col_names = ", ".join(all_cols)
    values = [wacc_dict[c] for c in all_cols]

    cursor = conn.execute(
        f"INSERT INTO wacc_inputs ({col_names}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cursor.lastrowid


# ── Raw payloads ────────────────────────────────────────────────────

def store_raw_payload(
    conn: sqlite3.Connection,
    run_id: str,
    source: str,
    entity_ref: str,
    payload: str | dict,
) -> int:
    """Store a raw API response for auditability."""
    if isinstance(payload, dict):
        payload = json.dumps(payload)

    cursor = conn.execute(
        "INSERT INTO raw_payloads (run_id, source, entity_ref, payload) VALUES (?, ?, ?, ?)",
        (run_id, source, entity_ref, payload),
    )
    conn.commit()
    return cursor.lastrowid


# ── Pipeline runs ───────────────────────────────────────────────────

def start_run(conn: sqlite3.Connection, run_id: str, arm: str, targets: list, config: dict = None) -> int:
    """Record a pipeline run start."""
    cursor = conn.execute(
        """INSERT INTO pipeline_runs (run_id, arm, input_targets, config_snapshot)
           VALUES (?, ?, ?, ?)""",
        (run_id, arm, json.dumps(targets), json.dumps(config) if config else None),
    )
    conn.commit()
    return cursor.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, output_path: str = None, error: str = None):
    """Update a pipeline run as completed or failed."""
    conn.execute(
        """UPDATE pipeline_runs
           SET status = ?, output_path = ?, error_message = ?, completed_at = CURRENT_TIMESTAMP
           WHERE run_id = ?""",
        (status, output_path, error, run_id),
    )
    conn.commit()
