"""SQLite connection management."""

import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "brandy.db")


def get_connection(db_path: str = None) -> sqlite3.Connection:
    """Return a SQLite connection with WAL mode and foreign keys enabled."""
    path = db_path or os.environ.get("BRANDY_DB_PATH", DEFAULT_DB_PATH)
    path = os.path.abspath(path)
    logger.info("Connecting to database: %s", path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _apply_schema_upgrades(conn: sqlite3.Connection):
    """
    Add columns that were introduced after the initial schema.

    Each entry is a (table, column, definition) triple. The upgrade is
    skipped silently if the column already exists, so this is safe to run
    on every startup regardless of the DB's age.
    """
    upgrades = [
        ("social_comments",   "comment_like_count",  "INTEGER DEFAULT 0"),
        ("brand_snapshots",   "filing_date",         "TEXT"),
        ("brand_snapshots",   "eps_diluted",         "REAL"),
        ("brand_snapshots",   "total_likes",         "INTEGER"),
        ("brand_snapshots",   "total_comments",      "INTEGER"),
        # WACC inputs sourced from filings, plus gross-profit provenance
        ("financial_metrics", "pretax_income",       "REAL"),
        ("financial_metrics", "short_term_debt",     "REAL"),
        ("financial_metrics", "long_term_debt",      "REAL"),
        ("financial_metrics", "total_debt",          "REAL"),
        ("financial_metrics", "interest_expense",    "REAL"),
        ("financial_metrics", "gross_profit_source", "TEXT"),
        ("financial_metrics", "gross_profit_notes",  "TEXT"),
        ("financial_metrics", "sga_expense",         "REAL"),
        ("financial_metrics", "operating_income_source", "TEXT"),
        ("financial_metrics", "operating_lease_liability_current",    "REAL"),
        ("financial_metrics", "operating_lease_liability_noncurrent", "REAL"),
    ]

    for table, column, definition in upgrades:
        existing = [
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.commit()
            logger.info("Schema upgrade applied: %s.%s %s", table, column, definition)


def init_db(db_path: str = None) -> sqlite3.Connection:
    """Create all tables if they don't exist, apply any schema upgrades, and return the connection."""
    from brandy.db.schema import CREATE_TABLES

    conn = get_connection(db_path)
    cursor = conn.cursor()
    for statement in CREATE_TABLES:
        cursor.execute(statement)
    conn.commit()
    logger.info("Database initialized with %d tables", len(CREATE_TABLES))

    _apply_schema_upgrades(conn)
    return conn
